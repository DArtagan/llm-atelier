# contacts-merge

Compare and merge Google Contacts between two Gmail accounts via CSV export/import.

## Usage

1. Export contacts from both accounts at [contacts.google.com](https://contacts.google.com) (Export -> Google CSV)
2. Run the merge tool:

```bash
python3 contacts_merge.py old-account.csv new-account.csv -o merged.csv
```

3. Import `merged.csv` at contacts.google.com (logged into the new account)
4. Delete all contacts from the old account

## Options

- `--dry-run` — show summary without interactive review
- `--report-dupes` — warn about duplicates within each account
- `--match-threshold 0.85` — tune fuzzy name matching (0.0-1.0)
- `-o FILE` — output path (default: `merged-contacts.csv`)

## Dependencies

Python 3.10+ (stdlib only, no pip packages).
