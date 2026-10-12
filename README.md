# Futures Desk · Circle

A private, read-only view of the Futures Desk for friends and family:
daily futures briefs, trade setups, release reactions, sector rotation and the
trade journal, plus live ratings and comments on each setup.

## How it stays private

- **Everything is encrypted.** `feed.enc.json` is AES-256-GCM ciphertext. The key
  comes from the access code (PBKDF2-SHA256, 600,000 iterations), so without the
  code there's nothing readable in this repo or on the site.
- **Comments are encrypted too.** They're stored in Supabase (free tier), encrypted
  in each visitor's browser with the same code. Supabase only accepts new posts
  that carry a token derived from the code.
- The page has `noindex` so search engines skip it.

## Files

| File | What it is |
|---|---|
| `index.html` | The site: unlock screen, then Today / History / Trade journal |
| `feed.enc.json` | The encrypted feed, republished after every scan |
| `publish.py` | Builds and encrypts the feed from journal exports |
| `supabase-setup.sql` | One-time database setup for live comments |

## Changing the access code

1. Pick a new code (16 letters/digits, e.g. `ABCD-EFGH-JKLM-NPQR`).
2. Ask Claude to "rotate the Futures Desk Circle access code". It will
   re-encrypt the feed, update the publishing task, and give you the new
   Supabase hash. Or do it yourself:
   ```
   FD_ACCESS_CODE=NEWCODE python3 publish.py --data ./export --check
   python3 -c "import hashlib;n='NEWCODE'.replace('-','').upper();t=hashlib.sha256(('fd-write:'+n).encode()).hexdigest();print(hashlib.sha256(t.encode()).hexdigest())"
   ```
   then replace the hash in the "post with access code" policy in Supabase.
3. Share the new code. Old comments stay encrypted under the old code and
   disappear from view, so tell people.

## Not advice

The content is one trader's hypotheses shared for discussion. It is not
investment advice. Don't charge for access: selling trade recommendations
can require registration as an investment adviser.
