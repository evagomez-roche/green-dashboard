from setuptools import setup, find_packages

setup(
    name="roche_green_sdk",
    version="2.0.1",  # Subimos versión por el parche
    description="Green Software Telemetry SDK for Digital Sustainability",
    author="Green Coding Team",
    packages=find_packages(),
    install_requires=[
        "codecarbon>=2.3.1",
        "requests>=2.28.0"
    ],
    python_requires=">=3.8",
)