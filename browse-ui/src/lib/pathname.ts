export function normalizeAppPathname(pathname: string | null | undefined): string {
  if (!pathname) return "/";
  const trimmed = pathname.trim();
  if (!trimmed) return "/";
  if (trimmed.length > 1 && trimmed.endsWith("/")) {
    return trimmed.slice(0, -1);
  }
  return trimmed;
}

export function matchesAppPath(pathname: string | null | undefined, href: string): boolean {
  const normalized = normalizeAppPathname(pathname);
  return normalized === href || normalized.startsWith(`${href}/`);
}
