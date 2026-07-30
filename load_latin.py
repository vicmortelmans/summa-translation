#!/usr/bin/env python3
"""
Script to populate the LatinByLemma table from an XML file.

Usage:
    python load_latin.py [xml_file] [num_elements]

Args:
    xml_file: Path to the XML file (default: xml_latin_nl.xml)
    num_elements: Number of lemma elements to process (default: process all)

The script reads <lemma> elements from the XML and inserts them into
the LatinByLemma table using the <reference> element as id and <latin> as text.
"""

import sqlite3
import xml.etree.ElementTree as ET
import sys
import argparse
from pathlib import Path


def load_lemmas_from_xml(xml_file, num_elements=None):
    """
    Extract lemma elements from XML file.
    
    Args:
        xml_file: Path to the XML file
        num_elements: Maximum number of lemmas to extract (None for all)
        
    Yields:
        Tuple of (reference_id, latin_text)
    """
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    count = 0
    for lemma in root.findall('.//lemma'):
        if num_elements is not None and count >= num_elements:
            break
            
        reference_elem = lemma.find('reference')
        latin_elem = lemma.find('latin')
        
        if reference_elem is not None and latin_elem is not None:
            reference_id = reference_elem.text
            latin_text = latin_elem.text
            
            if reference_id and latin_text:
                yield (reference_id, latin_text)
                count += 1


def populate_database(db_file, xml_file, num_elements=None, verbose=False):
    """
    Populate the LatinByLemma table from XML file.
    
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
        
        for reference_id, latin_text in load_lemmas_from_xml(xml_file, num_elements):
            try:
                cursor.execute(
                    'INSERT INTO LatinByLemma (Id, Text) VALUES (?, ?)',
                    (reference_id, latin_text)
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
        
        if verbose or True:  # Always show summary
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
        description='Populate LatinByLemma table from XML file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python load_latin.py                           # Uses default xml_latin_nl.xml, processes all
  python load_latin.py data.xml                  # Processes all lemmas from data.xml
  python load_latin.py data.xml 10               # Processes first 10 lemmas from data.xml
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
    
    # Validate inputs
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
    
    # Populate the database
    populate_database(
        args.database,
        str(xml_path),
        args.num_elements,
        args.verbose
    )


if __name__ == '__main__':
    main()
