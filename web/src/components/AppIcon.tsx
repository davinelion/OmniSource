"use client";

import { useState } from "react";
import type { AltApp } from "@/lib/data";
import { safeExternalUrl } from "@/lib/url";

export function tint(app: AltApp): string {
  const raw = (app.tintColor ?? "FF0000").replace("#", "");
  return /^[0-9A-Fa-f]{6}$/.test(raw) ? `#${raw}` : "#FF0000";
}

/**
 * App icon with a reserved box and a graceful failure path.
 *
 * The frame is fixed (`size`), so a slow or missing icon can never reflow the
 * card; `loading="lazy"` + `decoding="async"` keep a long grid cheap; and when
 * the URL 404s (feeds mirror icons, they do not own them) the first letter of
 * the name is drawn on the app's tint instead of a broken-image glyph. The URL
 * itself is validated, because feed data is untrusted.
 */
export default function AppIcon({
  app,
  size = 56,
  className = "",
}: {
  app: AltApp;
  size?: number;
  className?: string;
}) {
  const src = safeExternalUrl(app.iconURL);
  const [failed, setFailed] = useState(!src);
  const style = { background: tint(app), width: size, height: size };

  if (failed) {
    return (
      <span
        aria-hidden="true"
        className={`flex shrink-0 items-center justify-center rounded-xl font-bold text-white ${className}`}
        style={style}
      >
        {(app.name ?? "?").slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    // Icons are arbitrary third-party URLs, so next/image optimisation is off
    // (see next.config.ts) and the dimensions are reserved by the fixed box.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={`shrink-0 rounded-xl object-cover ${className}`}
      style={style}
    />
  );
}
