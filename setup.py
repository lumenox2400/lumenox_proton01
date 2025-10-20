from glob import glob
from setuptools import setup, find_packages
from os.path import splitext, basename
import versioneer

setup(
    name = 'lumenox_proton01',
    author = 'mateopereze',
    author_email = 'mateo.pereze4@gmail.com',
    description = 'Este proyecto se centra en la recolección automatizada de información a través de técnicas de web scraping.',
    url = 'https://github.com/mateopereze/lumenox_proton01',
    license = '...',
    package_dir = {'': 'src'},
    py_modules = [splitext(basename(path))[0] for path in glob('src/*.py')],
    python_requires = '>=3.11',
    classifiers = [
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    packages=find_packages(where='src'),
    install_requires = [
        'beautifulsoup4 >= 4.13.5',
        'gspread >= 6.2.1',
        'numpy >= 2.3.3',
        'oauth2client >= 4.1.3',
        'pandas >= 2.3.2',
        'playwright >= 1.55.0'
    ]
)
