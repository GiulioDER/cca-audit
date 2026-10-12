# Architecture diagram

An interactive HTML diagram of how CCA-Audit works, generated with
[Archify](https://github.com/tt-a1i/archify). Each box carries links to the exact source lines it
describes, so a reader can check every claim against the code.

| File | What it is |
|---|---|
| [cca-overview.html](cca-overview.html) | The interactive page: click a box to see its source lines. |
| [cca-overview.architecture.json](cca-overview.architecture.json) | The editable source the page is generated from. |
| `cca-overview-light.png`, `cca-overview-dark.png` | The diagram alone, shown in the project README. |

GitHub shows an HTML file as source rather than as a page, so download `cca-overview.html` and open
it in a browser. It needs no install and works offline, apart from the source links. The page has
light and dark themes, path tracing, and export to PNG and SVG.

## What it describes

The diagram is pinned to commit `f3f47d8e087c7d6f5ca039f505c687a4a40d06a0`. Its source links point
at that commit, so they keep resolving after master moves, and they describe the code as it was
then. When the code moves on, regenerate it rather than editing the HTML.

It shows the two entry points, every recorded layer from L0.4 to L6, the deterministic settlers
behind L2.5, and the enforcement path: `pipeline record` writes `run-state.json`, and the commit
guard and the Stop guard read it. The page's cards cover the tier matrix and what is left out: the
L2.6 scorecard, the `no-fix` and `p1-only` modes, the deferred pass, and pause and resume.

## Regenerating

The JSON is the editable source. Archify validates it, renders the page, and checks every cited
path and line range against the git objects at the pinned commit. With Archify 3.0.1 installed
(`npx skills add tt-a1i/archify -g`), run from the repository root, replacing `<archify>` with the
installed package's directory:

```bash
node <archify>/bin/archify.mjs finalize architecture docs/diagrams/cca-overview.architecture.json docs/diagrams/cca-overview.html --repo-root . --quality showcase --json
```

The two PNGs are the diagram alone, without the page around it, captured at twice the resolution
with the page open as `cca-overview.html?embed=1&theme=light` and `?embed=1&theme=dark`. Replace
both when the diagram changes.

To move to a newer commit, update `meta.repository.revision` in the JSON, re-check every cited line
range against that commit, and run the command again. Archify writes receipts beside the page; this
folder's `.gitignore` keeps them out of the tree, because they record local paths.
