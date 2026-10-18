# Web agent guide (React 19, Vite, Tailwind v4, shadcn/ui, pnpm)

Run from `web/`: `pnpm dev`, `pnpm test` (vitest + Testing Library), `pnpm typecheck`,
`pnpm lint` (oxlint), `pnpm format` (prettier), `pnpm build` (→ `dist/`, served by the backend).
Acceptance for any change: `pnpm typecheck && pnpm test && pnpm lint && pnpm exec prettier --check src && pnpm build`.

## Map (`src/`)

- `App.tsx`: the tabs Setup | Session | History | Campaign. `lib/route.ts`: the URL mirrors navigation.
- `lib/types.ts`: **the API contract** (mirrors `backend/src/troubadour/server.py`); change both together.
- `lib/api.ts`: every HTTP call. `lib/store.ts`: the live session (WebSocket events → state).
  `lib/campaignStore.ts`: campaign state + its **write queue**. `lib/record.ts`: saved records → view data.
- `components/session/`: live view (`ChatFeed` virtualized with react-virtuoso, `PlayerGrid`,
  `VoicesPanel`). `components/history/`: saved sessions, read-only viewer, transcript tools.
  `components/campaign/`: glossary, editor, import, suggestions, Ask. The entry editor opens in a non-modal
  right-hand `Sheet` (selection lives in `campaignStore`, mirrored in the URL). `EntityPicker` is a fuzzy
  `Command` combobox (aliases and heard-as spellings match). `components/transcript/`: rows shared by
  live and history views. `components/ui/`: shadcn primitives.
- `test/`: `setup.ts`, `fixtures.ts` (builders), `mockFetch.ts`.

## Rules

- **The live store holds only the live session.** The History viewer and Campaign tab keep their own
  state; WebSocket events must never leak into them.
- **Campaign writes go through the queue** in `campaignStore.ts`, computed from the latest entry when
  they run, and each response is applied before the next write. Review actions use the atomic
  `POST …/review` / `…/ignore` endpoints. Never PATCH a list from a stale copy.
- Text offsets from the API (`fixes`) are UTF-16; JS string indices already are.
- `messages.updated` patches may carry `text` and `fixes`; replace those wholesale.
- Long lists are virtualized; don't render all messages.
- The app never autoplays audio (`preload="none"`; play only on a click).
- Tests: Testing Library's `waitFor` (not `vi.waitFor` inside `act`); stub `fetch` with `mockFetch`;
  clear sonner toasts between tests. Every new behavior gets a test.
- Use shadcn/ui primitives and Tailwind tokens; keep UI text short and plain.
- Adding shadcn components: `pnpm dlx shadcn@latest add <name>` (without `--overwrite`). The CLI has
  rewritten `cn` imports to a bogus `cn` npm package and reformatted existing files; check
  `git diff` and keep imports as `@/lib/utils`.
