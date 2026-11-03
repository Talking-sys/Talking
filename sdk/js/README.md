# talking-sdk (JavaScript / TypeScript)

MIT client (see `sdk/NOTICE.md`). ESM, Node 18+ and browsers. Types in `src/index.d.ts`.

```js
import { TalkingClient } from "talking-sdk";

const client = new TalkingClient({
  serverUrl: process.env.TALKING_SERVER_URL,
  apiKey: process.env.TALKING_API_KEY, // never hard-code
});
client.setTargetLanguage("fr");
const r = await client.translateText("Keep moving.", { source: "en" });
console.log(r.translation);
```

Pass `{ fetch }` in tests. Pass `{ signal: AbortSignal }` to cancel. `offline: true` skips the network and uses a cached language list if you fetched one earlier.

```bash
node --test sdk/js/tests/client.test.js
```
