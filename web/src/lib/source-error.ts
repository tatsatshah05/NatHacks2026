// The one plain sentence the city page shows for a scheduled source's last error.
//
// GET /api/status carries each source's `last_error` exactly as the scheduler
// recorded it (cloud/app/scheduler.py): "timed out after 10 s",
// "<ExceptionClass>: <message>" or "store: <ExceptionClass>: <message>". The message
// is httpx's or the adapter's own text, with the request URL, the status reason
// and the MDN link httpx appends. None of that belongs on a page, so the text is
// mapped to one sentence by its class or status code and the raw text is never
// printed. It stays in the API response for anyone reading that directly.

const KEEPS = "OpenPath keeps its last known items.";

// Exception class names at the start of the text, as the scheduler writes them.
const TIMEOUT_CLASSES = /^(ReadTimeout|ConnectTimeout|WriteTimeout|PoolTimeout|TimeoutException|TimeoutError)\b/;
const NETWORK_CLASSES =
  /^(ConnectError|RemoteProtocolError|LocalProtocolError|ReadError|WriteError|NetworkError|ProxyError|UnsupportedProtocol|TransportError|ConnectionError|ConnectionResetError|ConnectionRefusedError|OSError)\b/;
const ANSWER_CLASSES = /^(JSONDecodeError|DecodingError|ValidationError|ValueError|KeyError|TypeError|UnicodeDecodeError)\b/;
// httpx: "HTTPStatusError: Client error '429 Too Many Requests' for url 'https://...'".
const HTTPX_STATUS = /\b(\d{3}) [A-Za-z][A-Za-z' -]*' for url/;
// An adapter's own words, for example "answered 503" or "HTTP 429".
const PLAIN_STATUS = /\b(?:status|answered|HTTP)\s+(\d{3})\b/i;

/** The sentence for `last_error`, or null when there is no error. Never the raw text. */
export function sourceErrorSentence(lastError: string | null | undefined): string | null {
  if (!lastError || !lastError.trim()) return null;
  const text = lastError.trim();
  if (text.startsWith("store:")) return `The result could not be saved to the database; ${KEEPS}`;
  const status = statusCode(text);
  if (status !== null) return statusSentence(status);
  if (/^timed out\b/i.test(text) || TIMEOUT_CLASSES.test(text)) return `The source did not answer in time; ${KEEPS}`;
  if (NETWORK_CLASSES.test(text)) return `The source could not be reached; ${KEEPS}`;
  if (ANSWER_CLASSES.test(text)) return `The source sent an answer OpenPath could not read; ${KEEPS}`;
  return `The source could not be read; ${KEEPS}`;
}

function statusCode(text: string): number | null {
  const match = HTTPX_STATUS.exec(text) ?? PLAIN_STATUS.exec(text);
  if (!match) return null;
  const code = Number(match[1]);
  return code >= 400 && code <= 599 ? code : null;
}

function statusSentence(code: number): string {
  if (code === 429) return `The source refused the request (too many requests); ${KEEPS}`;
  if (code === 401 || code === 403) return `The source refused the request (access denied); ${KEEPS}`;
  if (code === 404 || code === 410) return `The source answered that the data was not found (status ${code}); ${KEEPS}`;
  if (code < 500) return `The source refused the request (status ${code}); ${KEEPS}`;
  return `The source had a server problem (status ${code}); ${KEEPS}`;
}
