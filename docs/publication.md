# Source publication and licensing

On September 13, 2026, the owner approved publishing
[`tomdriley/fantasy-football`](https://github.com/tomdriley/fantasy-football)
under **GNU AGPL version 3 only**, SPDX identifier `AGPL-3.0-only`.
The full, unmodified license text is in [LICENSE](../LICENSE).
No automatic permission for a later license version is granted.

## License scope

Unless otherwise noted, AGPL-3.0-only applies to the project's original source.
It permits commercial use subject to its terms. Distribution and modified
network-service use carry corresponding-source obligations; consult the license
for the actual requirements. A source offer must correspond to the version
distributed or served, not merely whichever revision is currently on `main`.
New hosting images include the license and its OCI license identifier.

Third-party dependencies retain their own licenses and notices. API access,
downloaded datasets and third-party content are not relicensed by this project.
Sleeper's documented API terms distinguish non-commercial use from commercial
licensing. Yahoo access and data retention require their own approval. A software
license is not permission to bypass provider conditions or redistribute any
dataset without checking its applicable terms.

## Approved disclosure

The owner accepted publication of the configured league information obtained
from public APIs, including its historical copies. Moving that configuration
into a database remains an organization and multi-league-support improvement,
not a condition of this publication. No history or tag rewrite was requested.

The review baseline was `b33ae5a9282ddc5ae4fbc5a43e70aa082485aa4d`. Scoped hosted
and advisor security reviews reported no high-confidence vulnerabilities.
Advisory scans covered 187 JavaScript packages and 20 pinned Python packages,
with no known vulnerabilities reported at that time. Historical review covered
295 commits across local refs, distinguishing the 67-commit advertised remote
union, plus all 27 retained Actions run logs and 10 retained artifacts.
No actual credentials were found in those reviewed surfaces.

Local checkpoint refs are not automatically published by changing GitHub
visibility. They include actual-draft state that is absent from the advertised
remote object set: do not mirror or push local checkpoint refs indiscriminately.
Expired/deleted Actions records, unreachable Git objects, undisclosed
vulnerabilities and an exhaustive container-OS audit were outside the review.
These are scoped observations, not a guarantee of security or legal clearance.

## What remains private

- Google and database credentials in Key Vault.
- The deployed application's approved-account setting and database contents.
- Local caches, journals, evidence archives and uncommitted operator artifacts.
- The existing uncommitted Yahoo work until separately selected for a commit.

Public repository access does not bypass the application's Google sign-in or
backend authorization. The hosted app remains the synthetic write probe; the
full advisor still needs its separate authenticated hosting migration.

GitHub Actions logs and artifacts in a public repository must be considered
publicly accessible according to GitHub's access rules. The manual candidate
artifact steps remain private-repository-only. Use a private review workspace
or local build for confidential candidate audits. Retained artifacts from the
reviewed private period were included in the disclosure review. Historical
hosting documents describing a private repository record the earlier rollout
state; this publication decision supersedes that repository-visibility status,
not their credential-handling and deployment restrictions.
