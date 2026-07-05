"""
Import an emission-factor dataset — idempotent, provenance-tracked, optionally
activated. Datasets are never overwritten; a differing checksum for the same
(publisher, version, region) is rejected in favour of publishing a new version.
"""
import hashlib
import os
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.carbon.models import (
    ActivityType,
    EmissionFactor,
    EmissionFactorDataset,
    Publisher,
    Region,
)
from apps.carbon.services.importers import IMPORTER_REGISTRY, FactorImportError

User = get_user_model()


def _date(value):
    value = (value or "").strip()
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


class Command(BaseCommand):
    help = "Import (and optionally activate) an emission-factor dataset from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)
        parser.add_argument("--publisher", required=True, choices=[c[0] for c in Publisher.choices])
        parser.add_argument("--dataset-version", required=True, dest="dataset_version")
        parser.add_argument("--name", default="")
        parser.add_argument("--region", default="", help="Dataset region code (e.g. GB); blank = global")
        parser.add_argument("--valid-from", required=True, help="YYYY-MM-DD")
        parser.add_argument("--valid-to", default="")
        parser.add_argument("--publication-date", default="")
        parser.add_argument("--source-url", default="")
        parser.add_argument("--notes", default="")
        parser.add_argument("--imported-by", default="", help="Username to record as importer")
        parser.add_argument("--activate", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **o):
        path = o["file"]
        if not os.path.exists(path):
            raise CommandError(f"File not found: {path}")
        with open(path, "rb") as fh:
            raw = fh.read()
        checksum = hashlib.sha256(raw).hexdigest()
        text = raw.decode("utf-8-sig")
        filename = os.path.basename(path)

        region = None
        if o["region"]:
            region, _ = Region.objects.get_or_create(code=o["region"], defaults={"name": o["region"]})

        existing = EmissionFactorDataset.objects.filter(
            publisher=o["publisher"], version=o["dataset_version"], region=region
        ).first()
        if existing:
            if existing.checksum == checksum:
                self.stdout.write(
                    f"Dataset {o['publisher']} {o['dataset_version']} already imported "
                    f"(checksum match) — skipping."
                )
                return
            raise CommandError(
                "A dataset with this publisher/version/region exists with a DIFFERENT "
                "checksum. Datasets are immutable — publish a new version instead."
            )

        importer = IMPORTER_REGISTRY[o["publisher"]]()
        try:
            rows = importer.parse(text)
        except FactorImportError as exc:
            raise CommandError(str(exc))
        if not rows:
            raise CommandError("No factor rows parsed from the source file.")

        imported_by = User.objects.filter(username=o["imported_by"]).first() if o["imported_by"] else None

        with transaction.atomic():
            dataset = EmissionFactorDataset.objects.create(
                publisher=o["publisher"],
                name=o["name"] or f"{o['publisher']} {o['dataset_version']}",
                version=o["dataset_version"],
                region=region,
                status=EmissionFactorDataset.Status.DRAFT,
                valid_from=_date(o["valid_from"]),
                valid_to=_date(o["valid_to"]),
                publication_date=_date(o["publication_date"]),
                checksum=checksum,
                source_filename=filename,
                source_url=o["source_url"],
                imported_by=imported_by,
                import_notes=o["notes"],
            )

            at_map = {a.code: a for a in ActivityType.objects.all()}
            missing = sorted({r.activity_type_code for r in rows if r.activity_type_code not in at_map})
            if missing:
                raise CommandError(f"Unknown activity types (seed them first): {missing}")

            region_cache = {}
            factors = []
            for r in rows:
                row_region = region
                if r.region_code:
                    if r.region_code not in region_cache:
                        region_cache[r.region_code] = Region.objects.get_or_create(
                            code=r.region_code, defaults={"name": r.region_code}
                        )[0]
                    row_region = region_cache[r.region_code]
                factors.append(EmissionFactor(
                    dataset=dataset,
                    activity_type=at_map[r.activity_type_code],
                    region=row_region,
                    unit=r.unit,
                    co2e_per_unit=r.co2e_per_unit,
                    valid_from=r.valid_from,
                    valid_to=r.valid_to,
                    methodology=r.methodology,
                    source_ref=r.source_ref,
                ))
            EmissionFactor.objects.bulk_create(factors)

            if o["activate"]:
                EmissionFactorDataset.objects.filter(
                    publisher=o["publisher"], region=region,
                    status=EmissionFactorDataset.Status.ACTIVE,
                ).exclude(pk=dataset.pk).update(status=EmissionFactorDataset.Status.SUPERSEDED)
                dataset.status = EmissionFactorDataset.Status.ACTIVE
                dataset.save(update_fields=["status", "updated_at"])

            self.stdout.write(self.style.SUCCESS(
                f"Imported {len(factors)} factors into {dataset.publisher} {dataset.version} "
                f"[{dataset.status}] checksum={checksum[:12]}."
            ))
            if o["dry_run"]:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("DRY RUN — rolled back, nothing persisted."))

        if not o["dry_run"]:
            # Invalidate cached reference lists so the new factors are visible.
            from apps.carbon.cache_mixin import bump_refdata_version
            bump_refdata_version()
