"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

const LOCATION_CHANGE_EVENT = "browse:location-change";

function installLocationChangeBridge() {
  if (typeof window === "undefined") return;
  if (
    (window as typeof window & { __browseLocationBridgeInstalled?: boolean })
      .__browseLocationBridgeInstalled
  )
    return;

  const historyRef = window.history as History & {
    __browseOriginalPushState?: History["pushState"];
    __browseOriginalReplaceState?: History["replaceState"];
  };

  const dispatchLocationChange = () => window.dispatchEvent(new Event(LOCATION_CHANGE_EVENT));

  historyRef.__browseOriginalPushState = historyRef.pushState.bind(historyRef);
  historyRef.__browseOriginalReplaceState = historyRef.replaceState.bind(historyRef);

  historyRef.pushState = ((...args) => {
    const result = historyRef.__browseOriginalPushState!(...args);
    dispatchLocationChange();
    return result;
  }) as History["pushState"];

  historyRef.replaceState = ((...args) => {
    const result = historyRef.__browseOriginalReplaceState!(...args);
    dispatchLocationChange();
    return result;
  }) as History["replaceState"];

  (
    window as typeof window & { __browseLocationBridgeInstalled?: boolean }
  ).__browseLocationBridgeInstalled = true;
}

export function useResolvedPathname(): string {
  const pathname = usePathname();
  const [resolvedPathname, setResolvedPathname] = useState(() =>
    typeof window === "undefined" ? pathname : window.location.pathname || pathname
  );

  useEffect(() => {
    if (typeof window === "undefined") return;

    installLocationChangeBridge();
    const syncPathname = () => setResolvedPathname(window.location.pathname || pathname);

    syncPathname();
    window.addEventListener("popstate", syncPathname);
    window.addEventListener(LOCATION_CHANGE_EVENT, syncPathname);
    return () => {
      window.removeEventListener("popstate", syncPathname);
      window.removeEventListener(LOCATION_CHANGE_EVENT, syncPathname);
    };
  }, [pathname]);

  return resolvedPathname;
}
