/*
 * Install helpers (Phase 7): build per-client install URLs and expose a
 * copy helper used by module pages. The legacy clipboard delegation in
 * js/core.js stays the implementation for data-copy buttons, so this module
 * only fills in behaviour where no legacy script runs (standalone embeds).
 */

import { esc, siteUrl, translate } from './utils.js';

/* The five clients OmniSource supports, with the exact scheme each one
 * registers for "add this source". This table mirrors
 * src/omnisource/install.py::CLIENT_PROFILES — the Python side generates
 * feeds/install.json and the static app pages from it, so the two must not
 * drift. (They had: this file carried `altstore://add-source`,
 * `sidestore://addSource?url=` and `feather://addSource?url=`, none of which
 * any client registers for, plus `delta`/`walle` entries that are not
 * OmniSource clients at all — and no esign or livecontainer rows, so those
 * two silently fell through to the plain https feed URL with no deep link.)
 *
 * A `{url}` placeholder takes the percent-encoded feed URL. Feather is the
 * odd one out: it wants the bare host+path with no scheme and no query.
 */
export const CLIENT_SCHEMES = Object.freeze({
  altstore: 'altstore://source?url={url}',
  sidestore: 'sidestore://source?url={url}',
  feather: 'feather://source/{hostpath}',
  esign: 'esign://addsource?url={url}',
  livecontainer: 'livecontainer://sources?url={url}',
});

/** Every client id OmniSource ships deep links for, in display order. */
export const CLIENT_IDS = Object.freeze(Object.keys(CLIENT_SCHEMES));

/** The canonical subscribable feed URL for this deployment. */
export function sourceFeedUrl() {
  return siteUrl('apps.json');
}

/** `host/path` form Feather expects, with the scheme and query stripped. */
function hostPath(url) {
  try {
    const parsed = new URL(url);
    return (parsed.host + parsed.pathname) || url.split('://').pop();
  } catch (err) {
    return String(url).replace(/^[a-z]+:\/\//i, '');
  }
}

/* Percent-encode only what would actually break the link: a quote or angle
 * bracket would end the href attribute, and `#` / `&` / whitespace would end
 * or split the query. `:` and `/` stay intact on purpose so the result is
 * byte-identical to src/omnisource/install.py — which is what the generated
 * app pages and feeds/install.json ship. Several clients parse the query
 * naively and reject a fully encoded `https%3A%2F%2F…`, which is how ESign and
 * LiveContainer ended up behaving differently from AltStore and SideStore. */
function encodeFeedParam(url) {
  return String(url).replace(/["<>#&\s]/g, (ch) => encodeURIComponent(ch));
}

/** Install URL for one client (falls back to the plain feed URL). */
export function installUrlFor(client, feedUrl) {
  const url = feedUrl || sourceFeedUrl();
  const scheme = CLIENT_SCHEMES[String(client || '').toLowerCase()];
  if (!scheme) return url;
  return scheme
    .replace('{url}', encodeFeedParam(url))
    .replace('{hostpath}', hostPath(url));
}

/** Copy text to the clipboard with the pre-async fallback core.js uses. */
export async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (err) {
    /* fall through to the legacy path */
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    area.remove();
    return ok;
  } catch (err) {
    return false;
  }
}

/** Render a copy of the install box for embeds that ship no legacy JS. */
export function renderInstallBox(container) {
  if (!container) return;
  const feed = sourceFeedUrl();
  container.innerHTML =
    '<div class="panel install-embed">' +
    '<code>' + esc(feed) + '</code>' +
    '<button class="button small" type="button" data-copy="' + esc(feed) + '">' +
    esc(translate('hero.copySource', 'Copy')) +
    '</button></div>';
  const button = container.querySelector('[data-copy]');
  if (button) {
    button.addEventListener('click', async function () {
      const ok = await copyText(feed);
      button.textContent = ok
        ? translate('common.copied', 'Copied!')
        : translate('hero.copySource', 'Copy');
      setTimeout(function () {
        button.textContent = translate('hero.copySource', 'Copy');
      }, 1600);
    });
  }
}
