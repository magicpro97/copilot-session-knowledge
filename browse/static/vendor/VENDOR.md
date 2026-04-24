# Vendor Libraries

Vendored JS/CSS libs used by Hindsight browse UI.
Downloaded by `_download.py`. Do not edit manually.

| File | Package | Version | License | SHA-384 | Source |
|------|---------|---------|---------|---------|--------|
| `cytoscape.min.js` | cytoscape | 3.28.1 | MIT | `sha384-J7Q85oZE4GJ/e7+n2aOQsLXfDwwfnA8S2nZAL5BpFsfpCF84zQD7LroZ/dMnLgex` | [https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js](https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js) |
| `cytoscape-dagre.js` | cytoscape-dagre | 2.5.0 | MIT | `sha384-u69h9ebXeSjlg6q/rb1zKTRAGu/h8deCl0409xpS/QJctMKnc4M9Fzkm01VOQdeF` | [https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js](https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js) |
| `dagre.min.js` | dagre | 0.8.5 | MIT | `sha384-2IH3T69EIKYC4c+RXZifZRvaH5SRUdacJW7j6HtE5rQbvLhKKdawxq6vpIzJ7j9M` | [https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js](https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js) |
| `ninja-keys.min.js` | ninja-keys | 1.2.2 | MIT | `sha384-MOBmjJ9wSXr3Nbc9Zrites84GyyrDYleMho08qwyP3mco6Se8nkDiuAdz7W8t0Q1` | [https://cdn.jsdelivr.net/npm/ninja-keys@1.2.2/dist/ninja-keys.min.js](https://cdn.jsdelivr.net/npm/ninja-keys@1.2.2/dist/ninja-keys.min.js) |
| `pico.min.css` | @picocss/pico | 2.0.6 | MIT | `sha384-7P0NVe9LPDbUCAF+fH2R8Egwz1uqNH83Ns/bfJY0fN2XCDBMUI2S9gGzIOIRBKsA` | [https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css](https://cdn.jsdelivr.net/npm/@picocss/pico@2.0.6/css/pico.min.css) |

## Re-downloading

```bash
python browse/static/vendor/_download.py --force
```

## SRI usage

Use the SHA-384 values above as `integrity` attributes on `<script>` and `<link>` tags.
