#!/usr/bin/env python3
"""
Script to initialize the database using the schema defined in model.sql.

Usage:
    python create_db.py [database_file]

Args:
    database_file: Path to the database file to create (default: summa.db)
"""

import sqlite3
import sys
import argparse
from pathlib import Path


def create_database(db_file, schema_file='model.sql', force=False):
    """
    Create database from SQL schema file.
    
    Args:
        db_file: Path to the database file to create
        schema_file: Path to the SQL schema file
        force: If True, drop existing database before creating
        
    Raises:
        FileNotFoundError: If schema file doesn't exist
        sqlite3.Error: If database creation fails
    """
    schema_path = Path(schema_file)
    db_path = Path(db_file)
    
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")
    
    # Handle existing database
    if db_path.exists():
        if not force:
            print(f"Error: Database file already exists: {db_file}", file=sys.stderr)
            print("Use --force to overwrite", file=sys.stderr)
            sys.exit(1)
        db_path.unlink()
    
    # Read schema
    with open(schema_path, 'r', encoding='utf-8') as f:
        schema = f.read()
    
    # Create database and execute schema
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.executescript(schema)
        conn.commit()
        conn.close()
        print(f"Database created successfully: {db_file}")
    except sqlite3.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Initialize database from SQL schema file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_db.py                    # Creates summa.db
  python create_db.py mydb.db            # Creates mydb.db
  python create_db.py -f summa.db        # Recreates summa.db (force)
        """
    )
    
    parser.add_argument(
        'database',
        nargs='?',
        default='summa.db',
        help='Path to database file to create (default: summa.db)'
    )
    
    parser.add_argument(
        '-s', '--schema',
        default='model.sql',
        help='Path to SQL schema file (default: model.sql)'
    )
    
    parser.add_argument(
        '-f', '--force',
        action='store_true',
        help='Overwrite existing database file'
    )
    
    args = parser.parse_args()
    
    if args.database == '-h' or args.database == '--help':
        parser.print_help()
        sys.exit(0)
    
    try:
        create_database(args.database, args.schema, args.force)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
