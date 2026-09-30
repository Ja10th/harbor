# Harbor

A local cold-email desk. It is not Folio, and it is not a mail finder.

Type an industry and a location. Harbor searches the public web, keeps company sites, and groups them by how much of each site it could read: top, middle, thin. That is not a revenue ranking. It drafts an offer from a page it actually opened. An address is used only if the site published one. A public social page is opened the way a browser would, and only if the page itself shows a post. If the site asks for a login, that network is skipped. You can still paste a caption yourself if you opened one.

```
You type the address
        ↓
Research     the company site, not an inbox
        ↓
Draft        one sourced detail, no invented fact
        ↓
Approve      you, one letter at a time
        ↓
Send         your SMTP account, your name, your postal address
```

## Run

```bash
cd harbor
pip install -r requirements.txt
python server.py
```

Open http://localhost:8090

There is no login. Do not put this on a public server. The desk stores addresses and a mailbox password in `data/` on this computer.

## What you do first

1. Settings: your name, what you do, a postal address, and the mailbox you own.
2. Add a person, or import a CSV you already have: `name,email,company,title,website,reason`
3. Research, edit the detail if the page was thin, write the draft, approve, then send.

Gmail needs an app password: host `smtp.gmail.com`, port `587`, STARTTLS. The From address must be yours.

A practice row uses `practice@example.com`. Harbor will not send it. Use it to see a draft.

## What this will not do

- Guess or buy email addresses
- Scrape a feed or bypass a login wall
- Pretend you have been following the business
- Take a bought list and blast it
- Send as someone else, rotate inboxes, or hide behind proxies
- Add open-tracking pixels or rewrite copy to slip past spam filters
- Send a sequence while you are away

One follow-up is allowed, two days after the first letter, and only if you approve it. Daily cap is 40 at most, default 20, counted in Africa/Lagos. Password, payment, and account lures are refused.

You still need a lawful reason to email each person. Harbor does not decide that for you.

## Where the code is

- `server.py` — the desk
- `harbor/research.py` — public pages only
- `harbor/draft.py` — the letter
- `harbor/mailer.py` — one mailbox, one approved send
- `static/` — the page
