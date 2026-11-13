"""Copy installed dependency licenses into a Windows distribution."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys


def collect(destination):
    destination = Path(destination)
    packages = ['PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6',
                'openpyxl', 'xlrd', 'Pillow', 'httpx', 'httpcore', 'anyio',
                'certifi', 'idna', 'h11', 'typing_extensions', 'et_xmlfile', 'pyinstaller',
                'cryptography','cffi','pycparser']
    lines = ['Marketplace Transfer — сторонние компоненты', '']
    for package in packages:
        dist = metadata.distribution(package)
        lines.append(package + ' ' + dist.version)
        for file in dist.files or []:
            if ('licenses' in [part.casefold() for part in file.parts]
                    or file.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE'))):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = destination / 'licenses' / package / Path(*file.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    (destination / 'THIRD_PARTY_LICENSES.txt').write_text(
        '\n'.join(lines) + '\n\nТексты лицензий находятся в папке licenses.\n', encoding='utf-8')


if __name__ == '__main__':
    collect(sys.argv[1])
