from setuptools import setup

setup(
    name = 'PySeitchbot',
    packages = ['switchbot'],
    install_requires=['bluepy'],
    version = '0.1',
    description = 'A library to communicate with Switcbot',
    author='Daniel Hoyer Iversen',
    url='https://github.com/Danielhiversen/pySwitchbot/',
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Environment :: Other Environment',
        'Intended Audience :: Developers',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Topic :: Home Automation',
        'Topic :: Software Development :: Libraries :: Python Modules'
        ]
)