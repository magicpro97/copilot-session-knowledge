const APP_BASE_PATH = "/v2";

export function normalizeAppPathname(pathname: string | null | undefined): string {
  if (!pathname) return "/";
  const trimmed = pathname.trim();
  if (!trimmed) return "/";
  const withoutBasePath =
    trimmed === APP_BASE_PATH
      ? "/"
      : trimmed.startsWith(`${APP_BASE_PATH}/`)
        ? trimmed.slice(APP_BASE_PATH.length) || "/"
        : trimmed;
  if (withoutBasePath.length > 1 && withoutBasePath.endsWith("/")) {
    return withoutBasePath.slice(0, -1);
  }
  return withoutBasePath;
}

export function matchesAppPath(pathname: string | null | undefined, href: string): boolean {
  const normalized = normalizeAppPathname(pathname);
  return normalized === href || normalized.startsWith(`${href}/`);
}
