#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

if hasattr(os, "add_dll_directory"):
    _dll_path = r"C:\msys64\ucrt64\bin"
    if os.path.isdir(_dll_path):
        os.add_dll_directory(_dll_path)

def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'raven.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
