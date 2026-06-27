#!/usr/bin/env python3
"""Compare and merge Google Contacts CSV exports between two accounts."""

import argparse
import csv
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

REPEATING_GROUPS = ["E-mail", "Phone", "Address", "Organization", "Website",
                    "Relation", "Custom Field", "IM", "Event"]

SINGLE_FIELDS = ["First Name", "Middle Name", "Last Name",
                 "Phonetic First Name", "Phonetic Middle Name", "Phonetic Last Name",
                 "Name Prefix", "Name Suffix", "Nickname", "File As",
                 "Organization Name", "Organization Title", "Organization Department",
                 "Birthday", "Notes", "Photo", "Labels"]


@dataclass
class RepeatingEntry:
    group: str
    index: int
    fields: dict[str, str]

    def values_key(self):
        return tuple(sorted((k, v) for k, v in self.fields.items() if v))


@dataclass
class Contact:
    first_name: str = ""
    last_name: str = ""
    single_fields: dict[str, str] = field(default_factory=dict)
    repeating: list[RepeatingEntry] = field(default_factory=list)
    raw_row: dict[str, str] = field(default_factory=dict)

    @property
    def display_name(self):
        name = f"{self.first_name} {self.last_name}".strip()
        if name:
            return name
        for entry in self.repeating:
            if entry.group == "E-mail":
                val = entry.fields.get("Value", "")
                if val:
                    return f"(no name) <{val}>"
        for entry in self.repeating:
            if entry.group == "Phone":
                val = entry.fields.get("Value", "")
                if val:
                    return f"(no name) {val}"
        org = self.single_fields.get("Organization Name", "")
        if org:
            return f"(no name) [{org}]"
        return "(unnamed contact)"

    def emails(self) -> list[str]:
        return [e.fields["Value"].lower().strip()
                for e in self.repeating
                if e.group == "E-mail" and e.fields.get("Value", "").strip()]

    def phones(self) -> list[str]:
        return [normalize_phone(e.fields["Value"])
                for e in self.repeating
                if e.group == "Phone" and e.fields.get("Value", "").strip()]

    def summary_line(self) -> str:
        parts = [self.display_name]
        emails = [e.fields["Value"] for e in self.repeating
                  if e.group == "E-mail" and e.fields.get("Value", "").strip()]
        phones = [e.fields["Value"] for e in self.repeating
                  if e.group == "Phone" and e.fields.get("Value", "").strip()]
        if emails:
            parts.append(", ".join(emails))
        if phones:
            parts.append(", ".join(phones))
        return " | ".join(parts)

    def detail_lines(self) -> list[str]:
        lines = []
        org = self.single_fields.get("Organization Name", "")
        title = self.single_fields.get("Organization Title", "")
        if org:
            line = f"  Org: {org}"
            if title:
                line += f" ({title})"
            lines.append(line)
        for key in ["Nickname", "Birthday", "Notes"]:
            val = self.single_fields.get(key, "")
            if val:
                lines.append(f"  {key}: {val}")
        for entry in self.repeating:
            val = entry.fields.get("Value", "") or entry.fields.get("Formatted", "")
            if not val:
                parts = [v for v in entry.fields.values() if v]
                val = ", ".join(parts) if parts else ""
            if val:
                type_label = entry.fields.get("Label", "")
                label = f"{entry.group}"
                if type_label:
                    label += f" ({type_label})"
                lines.append(f"  {label}: {val}")
        return lines


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", name.lower().strip())


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("1") and len(digits) == 11:
        digits = digits[1:]
    return digits


GROUP_HEADER_RE = re.compile(r"^(.+?)\s+(\d+)\s+-\s+(.+)$")


def parse_google_csv(path: str) -> list[Contact]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return []
        headers = list(reader.fieldnames)
        contacts = []
        for row in reader:
            contact = Contact(
                first_name=row.get("First Name", ""),
                last_name=row.get("Last Name", ""),
                raw_row=dict(row),
            )
            for sf in SINGLE_FIELDS:
                val = row.get(sf, "")
                if val:
                    contact.single_fields[sf] = val

            seen_groups: dict[tuple[str, int], RepeatingEntry] = {}
            for header in headers:
                m = GROUP_HEADER_RE.match(header)
                if not m:
                    continue
                group_name, idx_str, field_name = m.group(1), m.group(2), m.group(3)
                if group_name not in REPEATING_GROUPS:
                    continue
                key = (group_name, int(idx_str))
                if key not in seen_groups:
                    entry = RepeatingEntry(group=group_name, index=int(idx_str), fields={})
                    seen_groups[key] = entry
                    contact.repeating.append(entry)
                seen_groups[key].fields[field_name] = row.get(header, "")

            contact.repeating = [e for e in contact.repeating
                                 if any(v for v in e.fields.values() if v)]
            contacts.append(contact)
    return contacts


def contacts_data_equal(a: Contact, b: Contact) -> bool:
    if a.single_fields != b.single_fields:
        return False
    a_entries = sorted(e.values_key() for e in a.repeating)
    b_entries = sorted(e.values_key() for e in b.repeating)
    return a_entries == b_entries


def diff_single_fields(old: Contact, new: Contact) -> list[tuple[str, str, str]]:
    diffs = []
    skip = {"First Name", "Last Name", "Middle Name", "Labels", "File As", "Photo"}
    all_keys = sorted(set(old.single_fields.keys()) | set(new.single_fields.keys()))
    for key in all_keys:
        if key in skip:
            continue
        old_val = old.single_fields.get(key, "")
        new_val = new.single_fields.get(key, "")
        if old_val != new_val:
            diffs.append((key, old_val, new_val))
    return diffs


def diff_repeating(old: Contact, new: Contact) -> dict[str, tuple[list[RepeatingEntry], list[RepeatingEntry], list[RepeatingEntry]]]:
    result = {}
    all_groups = sorted(set(e.group for e in old.repeating) | set(e.group for e in new.repeating))
    for group in all_groups:
        old_entries = [e for e in old.repeating if e.group == group]
        new_entries = [e for e in new.repeating if e.group == group]
        old_keys = {e.values_key() for e in old_entries}
        new_keys = {e.values_key() for e in new_entries}
        only_old = [e for e in old_entries if e.values_key() not in new_keys]
        only_new = [e for e in new_entries if e.values_key() not in old_keys]
        common = [e for e in new_entries if e.values_key() in old_keys]
        if only_old or only_new:
            result[group] = (only_old, only_new, common)
    return result


def format_repeating_entry(entry: RepeatingEntry) -> str:
    label = entry.fields.get("Label", "")
    val = entry.fields.get("Value", "") or entry.fields.get("Formatted", "")
    if not val:
        parts = [v for k, v in sorted(entry.fields.items()) if v and k != "Label"]
        val = ", ".join(parts) if parts else "(empty)"
    if label:
        return f"{label}: {val}"
    return val


@dataclass
class MatchResult:
    only_old: list[Contact]
    only_new: list[Contact]
    exact: list[tuple[Contact, Contact]]
    conflicts: list[tuple[Contact, Contact]]
    ambiguous: list[tuple[Contact, Contact, float]]


def match_contacts(old_contacts: list[Contact], new_contacts: list[Contact],
                   threshold: float = 0.85) -> MatchResult:
    exact = []
    conflicts = []
    ambiguous = []
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    def record_match(oi: int, ni: int):
        old_c, new_c = old_contacts[oi], new_contacts[ni]
        if contacts_data_equal(old_c, new_c):
            exact.append((old_c, new_c))
        else:
            conflicts.append((old_c, new_c))
        matched_old.add(oi)
        matched_new.add(ni)

    # Pass 1: email
    new_email_index: dict[str, list[int]] = {}
    for i, c in enumerate(new_contacts):
        for email in c.emails():
            new_email_index.setdefault(email, []).append(i)

    for oi in range(len(old_contacts)):
        for email in old_contacts[oi].emails():
            for ni in new_email_index.get(email, []):
                if ni not in matched_new:
                    record_match(oi, ni)
                    break
            if oi in matched_old:
                break

    # Pass 2: phone
    new_phone_index: dict[str, list[int]] = {}
    for i, c in enumerate(new_contacts):
        if i not in matched_new:
            for phone in c.phones():
                if phone:
                    new_phone_index.setdefault(phone, []).append(i)

    for oi in range(len(old_contacts)):
        if oi in matched_old:
            continue
        for phone in old_contacts[oi].phones():
            if not phone:
                continue
            for ni in new_phone_index.get(phone, []):
                if ni not in matched_new:
                    record_match(oi, ni)
                    break
            if oi in matched_old:
                break

    # Pass 3: fuzzy name
    for oi in range(len(old_contacts)):
        if oi in matched_old:
            continue
        old_c = old_contacts[oi]
        old_name = normalize_name(f"{old_c.first_name} {old_c.last_name}")
        if not old_name.strip():
            continue
        best_ratio = 0.0
        best_ni = -1
        for ni in range(len(new_contacts)):
            if ni in matched_new:
                continue
            new_c = new_contacts[ni]
            new_name = normalize_name(f"{new_c.first_name} {new_c.last_name}")
            if not new_name.strip():
                continue
            ratio = SequenceMatcher(None, old_name, new_name).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_ni = ni
        if best_ni < 0:
            continue
        if best_ratio >= threshold:
            record_match(oi, best_ni)
        elif best_ratio >= 0.7:
            ambiguous.append((old_contacts[oi], new_contacts[best_ni], best_ratio))
            matched_old.add(oi)
            matched_new.add(best_ni)

    only_old = [old_contacts[i] for i in range(len(old_contacts)) if i not in matched_old]
    only_new = [new_contacts[i] for i in range(len(new_contacts)) if i not in matched_new]

    return MatchResult(only_old=only_old, only_new=only_new, exact=exact,
                       conflicts=conflicts, ambiguous=ambiguous)


def prompt_choice(prompt_text: str, options: str) -> str:
    while True:
        try:
            choice = input(f"{prompt_text} [{options}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if choice and choice[0] in options:
            return choice[0]
        print(f"  Please enter one of: {', '.join(options)}")


def merge_contact_fields(old: Contact, new: Contact, field_choices: dict,
                         repeating_choices: dict) -> Contact:
    merged = Contact(
        first_name=new.first_name or old.first_name,
        last_name=new.last_name or old.last_name,
        single_fields=dict(new.single_fields),
        repeating=list(new.repeating),
        raw_row=dict(new.raw_row),
    )
    for key, choice in field_choices.items():
        if choice == "o":
            merged.single_fields[key] = old.single_fields.get(key, "")
        elif choice == "b":
            old_val = old.single_fields.get(key, "")
            new_val = new.single_fields.get(key, "")
            if old_val and new_val:
                merged.single_fields[key] = f"{new_val}\n---\n{old_val}"
            else:
                merged.single_fields[key] = new_val or old_val

    for group, choice in repeating_choices.items():
        if choice == "o":
            merged.repeating = [e for e in merged.repeating if e.group != group]
            merged.repeating.extend(e for e in old.repeating if e.group == group)
        elif choice == "b":
            existing_keys = {e.values_key() for e in merged.repeating if e.group == group}
            for entry in old.repeating:
                if entry.group == group and entry.values_key() not in existing_keys:
                    merged.repeating.append(entry)

    return merged


def renumber_repeating(contact: Contact) -> Contact:
    groups: dict[str, list[RepeatingEntry]] = {}
    for entry in contact.repeating:
        groups.setdefault(entry.group, []).append(entry)
    new_repeating = []
    for group, entries in groups.items():
        for i, entry in enumerate(entries, 1):
            new_repeating.append(RepeatingEntry(group=group, index=i, fields=dict(entry.fields)))
    contact.repeating = new_repeating
    return contact


def collect_all_headers(contacts: list[Contact]) -> list[str]:
    headers = list(SINGLE_FIELDS)
    seen = set(SINGLE_FIELDS)

    max_per_group: dict[str, int] = {}
    field_order_per_group: dict[str, list[str]] = {}
    for contact in contacts:
        for entry in contact.repeating:
            cur = max_per_group.get(entry.group, 0)
            if entry.index > cur:
                max_per_group[entry.group] = entry.index
            if entry.group not in field_order_per_group:
                field_order_per_group[entry.group] = []
            for fn in entry.fields:
                if fn not in field_order_per_group[entry.group]:
                    field_order_per_group[entry.group].append(fn)

    for group in REPEATING_GROUPS:
        max_idx = max_per_group.get(group, 0)
        field_names = field_order_per_group.get(group, [])
        for idx in range(1, max_idx + 1):
            for fn in field_names:
                h = f"{group} {idx} - {fn}"
                if h not in seen:
                    headers.append(h)
                    seen.add(h)

    return headers


def contact_to_row(contact: Contact, all_headers: list[str]) -> dict[str, str]:
    row = {h: "" for h in all_headers}

    row["First Name"] = contact.first_name
    row["Last Name"] = contact.last_name
    for key, val in contact.single_fields.items():
        if key in all_headers:
            row[key] = val

    for entry in contact.repeating:
        for field_name, val in entry.fields.items():
            header = f"{entry.group} {entry.index} - {field_name}"
            row[header] = val

    return row


def write_google_csv(contacts: list[Contact], path: str):
    contacts = [renumber_repeating(c) for c in contacts]
    all_headers = collect_all_headers(contacts)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_headers, extrasaction="ignore")
        writer.writeheader()
        for contact in contacts:
            row = contact_to_row(contact, all_headers)
            writer.writerow(row)


def find_intra_dupes(contacts: list[Contact], label: str) -> list[tuple[int, int]]:
    dupes = []
    for i in range(len(contacts)):
        for j in range(i + 1, len(contacts)):
            a, b = contacts[i], contacts[j]
            if set(a.emails()) & set(b.emails()):
                dupes.append((i, j))
                continue
            a_phones = set(p for p in a.phones() if p)
            b_phones = set(p for p in b.phones() if p)
            if a_phones & b_phones:
                dupes.append((i, j))
                continue
            a_name = normalize_name(f"{a.first_name} {a.last_name}")
            b_name = normalize_name(f"{b.first_name} {b.last_name}")
            if a_name and b_name and a_name == b_name:
                dupes.append((i, j))
    return dupes


def print_header(text: str):
    print(f"\n{BOLD}{CYAN}=== {text} ==={RESET}\n")


def print_subheader(text: str):
    print(f"\n{BOLD}--- {text} ---{RESET}\n")


def run_phase_ambiguous(result: MatchResult) -> None:
    if not result.ambiguous:
        return
    print_subheader(f"Phase 1: Confirm Possible Matches ({len(result.ambiguous)})")
    for i, (old_c, new_c, ratio) in enumerate(result.ambiguous):
        pct = int(ratio * 100)
        print(f"[{i+1}/{len(result.ambiguous)}] Is {YELLOW}\"{old_c.display_name}\"{RESET} (old) "
              f"the same person as {YELLOW}\"{new_c.display_name}\"{RESET} (new)?  "
              f"{DIM}({pct}% similar){RESET}")
        for line in old_c.detail_lines():
            print(f"  {RED}old{RESET} {line}")
        for line in new_c.detail_lines():
            print(f"  {GREEN}new{RESET} {line}")
        choice = prompt_choice("  Same person?", "yn")
        if choice == "y":
            if contacts_data_equal(old_c, new_c):
                result.exact.append((old_c, new_c))
            else:
                result.conflicts.append((old_c, new_c))
        else:
            result.only_old.append(old_c)
            result.only_new.append(new_c)
    result.ambiguous.clear()


def run_phase_conflicts(result: MatchResult) -> list[Contact]:
    merged = []
    if not result.conflicts:
        return merged
    print_subheader(f"Phase 2: Resolve Conflicts ({len(result.conflicts)})")
    for i, (old_c, new_c) in enumerate(result.conflicts):
        print(f"\n[{i+1}/{len(result.conflicts)}] {BOLD}\"{new_c.display_name}\"{RESET} — differences:")

        single_diffs = diff_single_fields(old_c, new_c)
        repeating_diffs = diff_repeating(old_c, new_c)

        if not single_diffs and not repeating_diffs:
            merged.append(new_c)
            continue

        for key, old_val, new_val in single_diffs:
            print(f"  {key}:")
            print(f"    {RED}old: {old_val or '(none)'}{RESET}")
            print(f"    {GREEN}new: {new_val or '(none)'}{RESET}")

        for group, (only_old_entries, only_new_entries, _common) in repeating_diffs.items():
            print(f"  {group}:")
            for e in only_old_entries:
                print(f"    {RED}old only: {format_repeating_entry(e)}{RESET}")
            for e in only_new_entries:
                print(f"    {GREEN}new only: {format_repeating_entry(e)}{RESET}")

        skip = prompt_choice(f"\n  Skip this contact entirely (keep new as-is)?", "yn")
        if skip == "y":
            print(f"  {DIM}Keeping new version as-is{RESET}")
            continue

        field_choices = {}
        for key, old_val, new_val in single_diffs:
            print(f"  {key}: {RED}{old_val or '(none)'}{RESET} vs {GREEN}{new_val or '(none)'}{RESET}")
            choice = prompt_choice(f"    Keep [o]ld / [n]ew / [b]oth", "onb")
            field_choices[key] = choice

        repeating_choices = {}
        for group, (only_old_entries, only_new_entries, _common) in repeating_diffs.items():
            print(f"  {group} entries differ:")
            for e in only_old_entries:
                print(f"    {RED}old only: {format_repeating_entry(e)}{RESET}")
            for e in only_new_entries:
                print(f"    {GREEN}new only: {format_repeating_entry(e)}{RESET}")
            choice = prompt_choice(f"    Keep [o]ld / [n]ew / [b]oth", "onb")
            repeating_choices[group] = choice

        m = merge_contact_fields(old_c, new_c, field_choices, repeating_choices)
        merged.append(m)
        print(f"  {GREEN}Merged.{RESET}")
    return merged


def run_phase_old_only(result: MatchResult) -> list[Contact]:
    imported = []
    if not result.only_old:
        return imported
    print_subheader(f"Phase 3: Review Old-Only Contacts ({len(result.only_old)})")
    print("These contacts exist only in the old account.")
    choice = prompt_choice("Import old-only contacts? [a]ll / [n]one / [r]eview each", "anr")
    if choice == "a":
        return list(result.only_old)
    if choice == "n":
        return []

    for i, contact in enumerate(result.only_old):
        print(f"\n[{i+1}/{len(result.only_old)}] {contact.summary_line()}")
        for line in contact.detail_lines():
            print(line)
        c = prompt_choice("  [y]es import / [n]o skip / [a]ll remaining / [d]one", "ynad")
        if c == "y":
            imported.append(contact)
        elif c == "a":
            imported.extend(result.only_old[i:])
            break
        elif c == "d":
            break
    return imported


def main():
    parser = argparse.ArgumentParser(
        description="Compare and merge Google Contacts CSV exports between two accounts.")
    parser.add_argument("old_csv", help="CSV exported from old Google account")
    parser.add_argument("new_csv", help="CSV exported from new Google account")
    parser.add_argument("-o", "--output", default="merged-contacts.csv",
                        help="Output CSV path (default: merged-contacts.csv)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show summary without interactive review")
    parser.add_argument("--report-dupes", action="store_true",
                        help="Report duplicate contacts within each account")
    parser.add_argument("--match-threshold", type=float, default=0.85,
                        help="Name match threshold 0.0-1.0 (default: 0.85)")
    args = parser.parse_args()

    print_header("Google Contacts Merger")

    print(f"Loading old account CSV: {args.old_csv}")
    old_contacts = parse_google_csv(args.old_csv)
    print(f"  {len(old_contacts)} contacts")

    print(f"Loading new account CSV: {args.new_csv}")
    new_contacts = parse_google_csv(args.new_csv)
    print(f"  {len(new_contacts)} contacts")

    if args.report_dupes:
        print_subheader("Duplicate Check")
        old_dupes = find_intra_dupes(old_contacts, "old")
        if old_dupes:
            print(f"{YELLOW}Old account has {len(old_dupes)} potential duplicate pair(s):{RESET}")
            for i, j in old_dupes:
                print(f"  - {old_contacts[i].display_name}  <->  {old_contacts[j].display_name}")
        new_dupes = find_intra_dupes(new_contacts, "new")
        if new_dupes:
            print(f"{YELLOW}New account has {len(new_dupes)} potential duplicate pair(s):{RESET}")
            for i, j in new_dupes:
                print(f"  - {new_contacts[i].display_name}  <->  {new_contacts[j].display_name}")
        if not old_dupes and not new_dupes:
            print("No duplicates found in either account.")

    print("\nMatching contacts...")
    result = match_contacts(old_contacts, new_contacts, threshold=args.match_threshold)

    print(f"  Exact matches:     {len(result.exact)}")
    print(f"  Conflicts:         {len(result.conflicts)}")
    print(f"  Possible matches:  {len(result.ambiguous)}")
    print(f"  Only in old:       {len(result.only_old)}")
    print(f"  Only in new:       {len(result.only_new)}")

    if args.dry_run:
        print(f"\n{DIM}(dry run — no changes made){RESET}")
        return

    run_phase_ambiguous(result)
    merged_conflicts = run_phase_conflicts(result)
    imported_old = run_phase_old_only(result)

    final_contacts = list(merged_conflicts) + list(imported_old)

    print_header("Output")
    print(f"Writing {len(final_contacts)} contacts to {args.output}")
    print(f"  {len(merged_conflicts)} merged (conflicts resolved)")
    print(f"  {len(imported_old)} imported from old account")
    print(f"  {len(result.exact)} exact matches (skipped — already in new)")
    print(f"  {len(result.only_new)} new-only (skipped — already in new)")

    write_google_csv(final_contacts, args.output)

    print(f"\n{GREEN}Done!{RESET} Next steps:")
    print(f"  1. Go to contacts.google.com (logged in as william@weiskopf.me)")
    print(f"  2. Import {args.output} (Google will merge with existing contacts)")
    print(f"  3. Spot-check a few merged contacts")
    print(f"  4. Then go to contacts.google.com as sirwilliamiv@gmail.com")
    print(f"     and delete all contacts there (select all -> delete)")


if __name__ == "__main__":
    main()
