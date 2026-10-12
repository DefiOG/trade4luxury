-- Futures Desk live comments: paste into Supabase -> SQL Editor -> Run.
-- Comments are encrypted in the browser with the access code; this table only
-- ever sees ciphertext. Posting requires a token derived from the access code.

create extension if not exists pgcrypto with schema extensions;

create table if not exists public.comments (
  id         bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  setup_key  text not null check (char_length(setup_key) = 32),
  payload    text not null check (char_length(payload) <= 4000),
  iv         text not null check (char_length(iv) <= 24),
  token      text not null
);

alter table public.comments enable row level security;

drop policy if exists "read encrypted comments" on public.comments;
create policy "read encrypted comments" on public.comments
  for select to anon using (true);

-- Only browsers that know the access code can post.
-- To change the access code later, replace the hash below (README explains how).
drop policy if exists "post with access code" on public.comments;
create policy "post with access code" on public.comments
  for insert to anon
  with check (encode(extensions.digest(token, 'sha256'), 'hex') = '5163d0cde96af2cadc0f1b9c9e606c5329bce24b42a51a4e5ea61e9ee61a1cae');

-- Visitors can read everything except the token column, and can't edit or delete.
revoke all on public.comments from anon, authenticated;
grant select (id, created_at, setup_key, payload, iv) on public.comments to anon;
grant insert (setup_key, payload, iv, token) on public.comments to anon;
