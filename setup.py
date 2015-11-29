from setuptools import find_packages
from setuptools import setup


setup(
    name='octoeb',
    version='1.2',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        'click',
        'requests',
    ],
    entry_points={
        'console_scripts': [
            'octoeb=octoeb.cli:cli'
        ]
    },
)
