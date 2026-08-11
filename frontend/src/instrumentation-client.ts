/**
 * Next.js 15 Dev Overlay / React component serialization may Object.keys()
 * the async `params` / `searchParams` Promise proxies. Our routes do not read
 * those props; filter that framework noise in development only.
 *
 * Install after a tick so we wrap outside Next's own console.error interceptor
 * (otherwise the overlay still surfaces the message).
 */
const SYNC_DYNAMIC_API_NOISE =
  /(`params`|`searchParams`).*(React\.use\(\)|unwrapped)|params are being enumerated|keys of `searchParams` were accessed directly/i;

const FILTER_MARK = Symbol.for("stock.syncDynamicApiFilter");

function isNoise(args: unknown[]): boolean {
  return args.some(
    (arg) => typeof arg === "string" && SYNC_DYNAMIC_API_NOISE.test(arg)
  );
}

function installFilter() {
  const current = console.error;
  if (
    typeof current === "function" &&
    FILTER_MARK in (current as object)
  ) {
    return;
  }
  const wrapped = (...args: unknown[]) => {
    if (isNoise(args)) return;
    Reflect.apply(current, console, args);
  };
  Object.defineProperty(wrapped, FILTER_MARK, { value: true });
  console.error = wrapped;
}

if (process.env.NODE_ENV === "development") {
  installFilter();
  queueMicrotask(installFilter);
  setTimeout(installFilter, 0);
  setTimeout(installFilter, 50);
}
