# Architectural Tradeoffs & Omissions (`TRADEOFFS.md`)

To ship a robust, maintainable, and reviewable first production release, we made deliberate architectural trade-offs. This document details three major features scoped out of the initial platform and the technical justifications behind these choices.

---

## 1. Omission: No Real-Time WebSockets or SSE for Upload Progress

### What was built instead
We utilize standard HTTP multipart file uploads and leverage Axios's native `onUploadProgress` browser callback to track file upload progress dynamically.

### Why this tradeoff was made
- **High System Complexity**: Implementing real-time Server-Sent Events (SSE) or WebSockets would require integrating Django Channels, setting up an active Redis instance as a channel layer, and managing long-lived asynchronous socket connections.
- **Minimal Workflow Value**: Corporate ESG data files (SAP reports, utility bills, corporate travel logs) are typically under 50MB. Under standard network conditions, HTTP uploads complete in under 5 seconds, making WebSockets an over-engineered addition that introduces potential system failure modes (e.g. connection drops, memory leaks).

---

## 2. Omission: No Multi-Factor Authentication (MFA) or SSO Integration

### What was built instead
We rely on standard Django session authentication and default User model permissions. Analysts are authenticated using DRF’s standard browsable authentication layer.

### Why this tradeoff was made
- **Extraneous Scope**: Setting up enterprise Single Sign-On (SSO) via SAML or OAuth2 requires external identity provider configurations (e.g., Okta, Auth0) and major security configurations.
- **Focus on Core ESG Operations**: The initial release prioritizes the data-engineering core (parsing, normalization, transactions, and audit-lock logic). Standing up enterprise SSO ahead of that core would consume delivery time without adding value to the primary ESG ingestion engine or validation rules. SSO/SAML is planned for a later hardening phase.

---

## 3. Omission: No Real-time Dynamic PDF OCR Ingestion

### What was built instead
The platform processes data from three structured digital channels: SAP CSV exports, Utility portal CSV tables, and Travel management JSON files.

### Why this tradeoff was made
- **High Dependency & Error Rates**: Running Optical Character Recognition (OCR) on scans of utility bills using external libraries (e.g. Tesseract, PDFPlumber) is highly prone to parsing errors due to varying invoice layout templates. It typically requires expensive machine learning models or third-party paid APIs.
- **Structured Reality**: Standard modern enterprise platforms pull utility bill tables directly from provider portals or energy brokers as digital CSV exports. Focusing on robust structured parsing strategy objects reflects realistic enterprise integration pipelines.
