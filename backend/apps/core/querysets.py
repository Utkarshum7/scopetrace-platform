from django.core.exceptions import ValidationError
from django.db import models


class SetNullCascadeSafeQuerySet(models.QuerySet):
    """
    Base for append-only/immutable models' QuerySets. Blocks bulk update()
    to stop business-field mutations from bypassing audit-trail/version-
    history/hash-chain creation (those are only ever produced by the
    model's own save()/clean(), which QuerySet.update() skips entirely by
    operating in raw SQL) -- while still allowing the ONE bulk update
    Django itself can issue against these models without going through any
    instance method: when a row this model has a SET_NULL foreign key to
    (e.g. a User referenced by `approved_by`) is deleted, the deletion
    Collector runs `<model>.objects.filter(pk__in=[...]).update(<field>=None)`
    to satisfy that FK -- see django.db.models.deletion.Collector.delete().
    That cascade only ever nulls fields actually declared on_delete=SET_NULL
    on this model, so whitelisting exactly that shape (and nothing else)
    lets e.g. User.delete() succeed for every user -- not just ones with no
    referencing rows -- without opening a path for real business-data bulk
    edits to sneak past the guard.

    Subclasses set `update_blocked_message` and still define their own
    `delete()` (hard-delete has no equivalent legitimate cascade to allow
    through -- SET_NULL cascades never delete rows on this side).
    """

    update_blocked_message = "Bulk update is not permitted on this model."

    def _set_null_cascade_fields(self):
        return {
            f.name
            for f in self.model._meta.fields
            if f.is_relation and getattr(f.remote_field, "on_delete", None) is models.SET_NULL
        }

    def update(self, **kwargs):
        if (
            kwargs
            and set(kwargs) <= self._set_null_cascade_fields()
            and all(value is None for value in kwargs.values())
        ):
            return super().update(**kwargs)
        raise ValidationError(self.update_blocked_message)
