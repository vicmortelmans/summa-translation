#!/usr/bin/env python3
"""
Script to populate the DutchByLemma table from an XML file.

Usage:
    python load_dutch.py [xml_file] [num_elements]

Args:
    xml_file: Path to the XML file (default: xml_latin_nl.xml)
    num_elements: Number of lemma elements to process (default: process all)

The script reads <lemma> elements from the XML and inserts them into
DutchByLemma using <reference> as Id and <nl> as Text.
Elements containing an <absend> child are skipped. Empty <nl> elements are
imported as an empty string.
"""

import sqlite3
import xml.etree.ElementTree as ET
import sys
import argparse
from pathlib import Path


def load_dutch_from_xml(xml_file, num_elements=None):
    """
    Extract lemma elements from XML file.

    Args:
        xml_file: Path to the XML file
        num_elements: Maximum number of lemmas to extract (None for all)

    Yields:
        Tuple of (reference_id, dutch_text)
    """
    tree = ET.parse(xml_file)
    root = tree.getroot()

    count = 0
    for lemma in root.findall('.//lemma'):
        if num_elements is not None and count >= num_elements:
            break

        if lemma.find('absend') is not None:
            continue

        reference_elem = lemma.find('reference')
        nl_elem = lemma.find('nl')

        if reference_elem is None or nl_elem is None:
            continue

        reference_id = reference_elem.text
        dutch_text = nl_elem.text or ''

        if reference_id is None:
            continue

        yield (reference_id, dutch_text)
        count += 1


def populate_database(db_file, xml_file, num_elements=None, verbose=False):
    """
    Populate the DutchByLemma table from XML file.

    Args:
        db_file: Path to the SQLite database file
        xml_file: Path to the XML file
        num_elements: Maximum number of lemmas to insert (None for all)
        verbose: Print progress information

    Returns:
        Number of rows inserted
    """
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()

        inserted = 0
        skipped = 0

        for reference_id, dutch_text in load_dutch_from_xml(xml_file, num_elements):
            try:
                cursor.execute(
                    'INSERT INTO DutchByLemma (Id, Text) VALUES (?, ?)',
                    (reference_id, dutch_text)
                )
                inserted += 1
                if verbose:
                    print(f"Inserted: {reference_id}")
            except sqlite3.IntegrityError as e:
                skipped += 1
                if verbose:
                    print(f"Skipped (duplicate or constraint): {reference_id} - {e}")

        conn.commit()
        conn.close()

        print(f"\nInsertion complete:")
        print(f"  Inserted: {inserted} rows")
        print(f"  Skipped: {skipped} rows")

        return inserted

    except sqlite3.OperationalError as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Populate DutchByLemma table from XML file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python load_dutch.py                           # Uses default xml_latin_nl.xml, processes all
  python load_dutch.py data.xml                  # Processes all lemmas from data.xml
  python load_dutch.py data.xml 10               # Processes first 10 lemmas from data.xml
        """
    )

    parser.add_argument(
        'xml_file',
        nargs='?',
        default='xml_latin_nl.xml',
        help='Path to XML file (default: xml_latin_nl.xml)'
    )

    parser.add_argument(
        'num_elements',
        nargs='?',
        type=int,
        default=None,
        help='Number of elements to process (default: all)'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Print detailed progress information'
    )

    parser.add_argument(
        '-d', '--database',
        default='summa.db',
        help='Path to SQLite database file (default: summa.db)'
    )

    args = parser.parse_args()

    xml_path = Path(args.xml_file)
    if not xml_path.exists():
        print(f"Error: XML file not found: {args.xml_file}", file=sys.stderr)
        sys.exit(1)

    if args.num_elements is not None and args.num_elements <= 0:
        print("Error: num_elements must be a positive integer", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"XML file: {xml_path.absolute()}")
        print(f"Database: {args.database}")
        if args.num_elements:
            print(f"Processing: first {args.num_elements} elements")
        else:
            print("Processing: all elements")
        print()

    populate_database(
        args.database,
        str(xml_path),
        args.num_elements,
        args.verbose
    )


if __name__ == '__main__':
    main()
