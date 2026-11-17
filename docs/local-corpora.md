# Private local document corpora

`graphify documents` builds a deterministic local document graph and a SQLite
FTS5 index. It never calls an LLM, downloads models, or makes network requests.
It does not modify source documents. Existing `extract` and global graph commands
are unchanged.

```bash
# The output directory is created private (0700), with private files (0600).
graphify documents index /path/to/documents --out ~/.graphify/corpora/documents
graphify documents search "invoice" --index ~/.graphify/corpora/documents
graphify documents status --index ~/.graphify/corpora/documents
# The graph can also be used with existing Graphify traversal.
graphify query "invoice" --graph ~/.graphify/corpora/documents/graph.json
```

The output must differ from the source root. An existing output directory must
already be private. A corpus is bound to one resolved source root. File size,
mtime and ctime detect changes. Removed files disappear from search on refresh.
Concurrent refreshes serialize using a local file lock. Full-text readers use a
read-only SQLite connection and never create a missing corpus accidentally.

## Extraction coverage

- UTF-8 text, common code/configuration and CSV: bounded text reading.
- PDF: local `pdftotext` executable, with a 30-second timeout per file.
- ODT, ODS, ODP, DOCX, XLSX, PPTX: local ZIP/XML text extraction. Expanded selected
  XML is capped at 20 MB. DTDs and entities are rejected. Unsupported XML
  encodings are reported as extraction errors, not silently processed unsafely.
- Other files, including legacy DOC/XLS, images, archives and audio: filename and
  path search only. No OCR, transcription, archive recursion or macro execution.
- Files larger than 50 MB are metadata-only with a `size_limit` status.
- Extracted content is limited to two million characters, with explicit
  `truncated` status. Empty/scanned PDFs have `no_text` status.

Cache/build/VCS directories, symlinks and known credential filenames are excluded.
Recognized credential assignments and private keys exclude a document's content
and its metadata from the corpus. This is defense in depth, not a guarantee that
arbitrary secrets can be automatically identified. Keep the corpus private.
**Do not merge private document graphs into a shared global graph.**

The report lists status counts, unsupported extensions and individual extraction
gaps. One malformed document does not prevent other files being indexed.
Office extraction preserves text and cached spreadsheet values, not layout or
cell relationships. Check original documents for accounting or legal decisions.

Search treats words as literal Unicode tokens. Ordinary text uses an AND of word
prefixes (numeric words are exact). Queries containing quotes, a hyphen or a dot
use an exact token phrase, useful for invoice identifiers and decimal amounts.
FTS operators in user input are not executed. Punctuation follows SQLite's
Unicode tokenization, so formatted numbers may have different tokens.

## Optional local refresh timer

A systemd user oneshot service can execute the same index command with
`UMask=0077`, `Nice=10`, `NoNewPrivileges=true` and
`RestrictAddressFamilies=AF_UNIX`. A timer with `OnStartupSec=5min` and
`OnUnitInactiveSec=15min` keeps it current while the user manager runs.
Use `StandardOutput=null` so private document names in reports do not enter the
journal. Validate the real service after enabling it. No system or production
service needs to restart.

## Verification evidence

The feature's contracts cover private permissions, credential/path exclusions,
symlinks, literal Unicode search, changes and deletions, root binding, ODF/OOXML,
malformed and entity-bearing XML, binary/text limits, CLI dispatch and blocked
network sockets. Fourteen focused tests pass with 97% module line coverage.
The combined detect/path/security/query/corpus regression suite passes 382 tests.
Real acceptance used a private local corpus, native graph traversal, incremental
refresh, SQLite integrity checks and a network-restricted user service. Private
source text, filenames and financial details are deliberately not recorded here.
