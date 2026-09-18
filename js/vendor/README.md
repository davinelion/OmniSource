# Vendored JavaScript

## `qrcode.min.js`

[qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) by
Kazuhiko Arase, version **2.0.4**, MIT licensed (`js/vendor/qrcode.js` is the
unmodified upstream `dist/qrcode.js` retained for reference; `qrcode.min.js` is
the same file minified with `terser --compress --mangle`, license header kept).

**Why vendored instead of fetched:** the site used to render every QR code with
an `<img>` pointing at `api.qrserver.com`. That is a third-party request per
visitor, it does not work offline, and it silently depended on a service
OmniSource does not control — the exact fragility the rest of the project
avoids for feeds and downloads. `OS.qrImage()` (`js/core.js`) now builds QR
codes locally and hands the page a `data:` URL, so the install center, the app
pages and the share dialog work with no network at all.

**Updating:** download the same file from a new release, keep the license
header, re-run the minify command above, and note the version here.
