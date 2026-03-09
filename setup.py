from setuptools import find_packages, setup

setup(
    name="owuinc",
    version="2.2.0",
    author="Duncan Nicholson",
    description="openwebui nextcloud integration",
    url="https://github.com/soakedcardinal/owuinc",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=["caldav", "icalendar", "webdavclient3", "pydantic"],
)
