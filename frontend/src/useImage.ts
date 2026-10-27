import { useEffect, useState } from "react";
import type { Api } from "./context";

export function useImage(api: Api, id: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null), [error, setError] = useState(false);
  useEffect(() => {
    const abort = new AbortController();
    let objectUrl: string | null = null, active = true;
    setUrl(null); setError(false);
    if (id) void api.blob(`/api/observations/${encodeURIComponent(id)}/image`, abort.signal)
      .then((blob) => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(() => {if (active && !abort.signal.aborted) setError(true);});
    return () => { active = false; abort.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [api, id]);
  return {url, error};
}
