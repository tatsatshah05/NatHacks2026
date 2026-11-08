"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

type Props = {
  /** Shown until the children mount. Give it the children's size, so nothing shifts. */
  placeholder: ReactNode;
  /** How far outside the viewport the children start loading, in CSS px. */
  marginPx?: number;
  children: ReactNode;
};

// OP-410: mount heavy children (the MapLibre maps) only when their place comes near the
// viewport. On a phone the map sits below the form or the list, so the first screen
// never pays for the map's code; on a laptop, where the map is beside the content, it
// mounts at once. Without IntersectionObserver the children mount straight away.
export default function LazyMount({ placeholder, marginPx = 400, children }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (ready) return;
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") {
      setReady(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setReady(true);
          observer.disconnect();
        }
      },
      { rootMargin: `${marginPx}px 0px` }
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ready, marginPx]);

  if (ready) return <>{children}</>;
  return <div ref={ref}>{placeholder}</div>;
}
