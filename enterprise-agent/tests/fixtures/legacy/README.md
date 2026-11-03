# Legacy Office fixtures

The three small binary documents are Apache POI test fixtures, copied from commit
`17de566ac64dee14bc1c132b9b4972b9a21165b3` of [apache/poi](https://github.com/apache/poi).
The upstream license and notice are included alongside them. No tests download data.

| Local file | Upstream path | SHA-256 |
| --- | --- | --- |
| `simple.doc` | `test-data/document/simple.doc` | `4876e828e62d490fa188c7c4e47013e2e0dfdf02f5903dfd374bc0d2b2049ca1` |
| `basic_test.ppt` | `test-data/slideshow/basic_test_ppt_file.ppt` | `e8fd4c38ad72ce3735acbca0b60dd5870fb60f819c169e3947d166f4f1bcc79d` |
| `simple.xls` | `test-data/spreadsheet/Simple.xls` | `2b5ec8298bacffdca61c50f6f7607da0a4e63cf76fa011c62f203e6a66bc072a` |

References describe readable body/slides/notes/cached worksheet values with the existing
reader's labels. The PPT reference excludes master-edit placeholders and obsolete fast-save
records. The XLS reference follows POI's `TestExcelExtractor.testSimple`, excluding empty
sheet labels as the existing XLSX reader does. The DOC reference is the document body.
These conversion references require validation by the opt-in real-container test, which is
not replaced by mocked converter output in default tests.

Additional self-authored fixtures are constructed deterministically in `document_corpus.py`:
two examples per native format, three EML and three CFB/MAPI MSG emails, two RTF and two ODT
documents. Those files contain synthetic content only. The MAPI writer uses documented CFB
and MAPI structures and does not execute or save attachments.
