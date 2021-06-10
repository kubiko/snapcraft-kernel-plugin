# Custom snapcraft kernel plugin
This custom plugin enables build of kernel snaps for Ubuntu Core 16/18/20.
Support for Ubuntu Core 20 kernel snap is provisional before new pluging v2 is completed.
In addition, plugin adds  additional customisation options. Please refer to the help within the plugin.

## Usage
There are two ways to use this custom plugin.
### Local Copy
Copy `kernel.py` to `snap/plugins/` directory in your kernel snap project.
### git submodules
`$ git submodule add https://github.com/kubiko/snapcraft-kernel-plugin.git snap/plugins`
