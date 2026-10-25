import os

from setuptools import find_packages, setup


def labextension_data_files():
    """Ship the built federated labextension + enablement config with the wheel.

    `data_files` entries land under sys.prefix when the wheel is installed,
    which is exactly where Jupyter looks for share/jupyter/labextensions and
    etc/jupyter/jupyter_server_config.d.
    """
    files = []
    dist = os.path.join("labextension", "jupyterlab-dsh-dist")
    target = os.path.join("share", "jupyter", "labextensions", "jupyterlab-dsh")
    for root, _dirs, names in os.walk(dist):
        rel_dir = os.path.relpath(root, dist)
        rel_dir = "" if rel_dir == "." else rel_dir
        payloads = [os.path.join(root, n) for n in names]
        if payloads:
            files.append((os.path.join(target, rel_dir), payloads))
    files.append(
        (
            os.path.join("etc", "jupyter", "jupyter_server_config.d"),
            ["etc/jupyter/jupyter_server_config.d/jupyterlab_dsh.json"],
        )
    )
    return files


setup(
    name="jupyterlab_dsh",
    version="0.2.0",
    description=(
        "DeepSeek Harness (dsh) tab for JupyterLab - same-origin reverse "
        "proxy server extension + launcher card labextension"
    ),
    packages=find_packages(exclude=["labextension"]),
    data_files=labextension_data_files(),
    install_requires=["jupyter_server>=1.8"],
    python_requires=">=3.7",
    zip_safe=False,
)
