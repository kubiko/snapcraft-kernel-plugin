# -*- Mode:Python; indent-tabs-mode:nil; tab-width:4 -*-
#
# Copyright (C) 2016-2018,2020 Canonical Ltd
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""The kernel plugin allows building kernel snaps
with all the bells and whistles in one shot...

The following kernel specific options are provided by this plugin:

    - kdefconfig:
      (list of kdefconfigs)
      defconfig target to use as the base configuration. default: "defconfig"

    - kconfigfile:
      (filepath)
      path to file to use as base configuration. If provided this option wins
      over everything else. default: None

    - kconfigflavour
      (string)
      Ubuntu config flavour to use as base configuration. If provided this
      option wins over kdefconfig. default: None

    - kconfigs
      (list of strings)
      explicit list of configs to force; this will override the configs that
      were set as base through kdefconfig and kconfigfile and dependent configs
      will be fixed using the defaults encoded in the kbuild config
      definitions.  If you don't want default for one or more implicit configs
      coming out of these, just add them to this list as well.

    - kernel-image-target:
      (yaml object, string or null for default target)
      the default target is bzImage and can be set to any specific
      target.
      For more complex cases where one would want to use
      the same snapcraft.yaml to target multiple architectures a
      yaml object can be used. This yaml object would be a map of
      debian architecture and kernel image build targets.

    - kernel-with-firmware:
      (boolean; default: True)
      use this flag to disable shipping binary firmwares

    - kernel-device-trees:
      (array of string)
      list of device trees to build, the format is <device-tree-name>.dts.

    - kernel-build-efi-image
      Optional, true if we want to create an EFI image, false otherwise (false
      by default)

    - kernel-compiler
      (string; default:)
      Optional, define compiler to use, by default gcc compiler is used
      Orher permited compilers: clang

    - kernel-compiler-paths
      (array of strings)
      Optional, define the compiler path to be added to the PATH.
      Path is relative to the stage directory.
      Default value is empty

    - kernel-compiler-parameters
      (array of string)
      Optional, define extra compiler parameters to be passed to the compiler
      Default value is empty

    - kernel-initrd-modules:
      (array of string)
      list of modules to include in initrd; note that kernel snaps do not
      provide the core boot logic which comes from snappy Ubuntu Core
      OS snap. Include all modules you need for mounting rootfs here.

    - kernel-initrd-configured-modules:
      (array of string)
      list of modules to be added to the initrd
      /lib/modules-load.d/ubuntu-core-initramfs.conf config
      to be automatically loaded.
      Configured modules are atomatically added to the kernel-initrd-modules
      If modules in question is not supported by the kernel, it's automatically
      removed

    - kernel-initrd-firmware:
      (array of string)
      list of firmware files to be included in the initrd; these need to be
      relative paths to stage directory.
      <stage/part instal dir>/firmware/* -> initrd:/lib/firmware/*

    - kernel-initrd-compression:
      (string; default: lz4)
      initrd compression to use; the only supported values now are
      'lz4', 'xz', 'gz'.

    - kernel-initrd-compression-options:
      Optional list of parameters to be passed to compressor used for initrd
      (array of string): defaults are
        gz:  -7
        lz4: -9 -l
        xz:  -7

    - kernel-initrd-channel
      Optional channel for uc-inird snap. Track is based on project's build-base
      This option is ignored if kernel-initrd-base-url is used!
      Default: stable

    - kernel-initrd-base-url
      Optional base url to be used to download reference inird from.
      e.g. https://people.canonical.com/~okubik/uc-initrds
      Default: none

    - kernel-initrd-flavour
      Optional parameter(Default flavour is none).
      This can be used only together with kernel-initrd-base-url to specify
      additional initrd flavour to download. Assembled url is:
      {kernel-initrd-base-url}/{uc-initrd}_{series}{flavour}_{architecture}.snap

    - kernel-initrd-overlay
      Optional overlay to be applied to built initrd
      This option is designed to provide easy way to apply initrd overlay for
      cases modifies initrd scripts for pre uc20 initrds.
      Value is relative path, in stage directory. and related part needs to be
      built before initrd part. During build it will be expanded to
      ${SNAPCRAFT_STAGE}/{initrd-overlay}
      Default: none

    - kernel-initrd-addons
      (array of string)
      Optional list of files to be added to the initrd.
      Function is similar to kernel-initrd-overlay, only it works on per file
      selection without need to have overlay in dedicated directory.
      This option is designed to provide easy way to add additonal content
      to initrd for cases like full disk encryption support, when device
      specific hook needs to be added to the initrd.
      Values are relative path from stage directory, so related part(s)
      needs to be built before kernel part.
      During build it will be expanded to
      ${SNAPCRAFT_STAGE}/{initrd-addon}
      Default: none

    - kernel-enable-zfs-support
      (boolean; default: False)
      use this flag to build in zfs support through extra ko modules
"""

import click
import os
import sys
import re

from snapcraft import ProjectOptions
from typing import Any, Dict, List, Set

from snapcraft.plugins.v2 import PluginV2

_compression_command = {"gz": "gzip", "lz4": "lz4", "xz": "xz"}
_compressor_options = {"gz": "-7", "lz4": "-l -9", "xz": "-7"}
_INITRD_URL = "{base_url}/{snap_name}"
_INITRD_SNAP_NAME = "uc-initrd"
_INITRD_SNAP_FILE = "{snap_name}_{series}{flavour}_{architecture}.snap"
_ZFS_URL = "https://github.com/openzfs/zfs"

default_kernel_image_target = {
    "amd64": "bzImage",
    "i386": "bzImage",
    "armhf": "zImage",
    "arm64": "Image.gz",
    "powerpc": "uImage",
    "ppc64el": "vmlinux.strip",
    "s390x": "bzImage",
    "riscv64": "Image",
}

required_generic = [
    "DEVTMPFS",
    "DEVTMPFS_MOUNT",
    "TMPFS_POSIX_ACL",
    "IPV6",
    "SYSVIPC",
    "SYSVIPC_SYSCTL",
    "VFAT_FS",
    "NLS_CODEPAGE_437",
    "NLS_ISO8859_1",
]

required_security = [
    "SECURITY",
    "SECURITY_APPARMOR",
    "SYN_COOKIES",
    "STRICT_DEVMEM",
    "DEFAULT_SECURITY_APPARMOR",
    "SECCOMP",
    "SECCOMP_FILTER",
    "CC_STACKPROTECTOR",
    "CC_STACKPROTECTOR_STRONG",
    "DEBUG_RODATA",
    "DEBUG_SET_MODULE_RONX",
]

required_snappy = [
    "RD_LZMA",
    "KEYS",
    "ENCRYPTED_KEYS",
    "SQUASHFS",
    "SQUASHFS_XATTR",
    "SQUASHFS_XZ",
    "DEVPTS_MULTIPLE_INSTANCES",
]

required_systemd = [
    "DEVTMPFS",
    "CGROUPS",
    "INOTIFY_USER",
    "SIGNALFD",
    "TIMERFD",
    "EPOLL",
    "NET",
    "SYSFS",
    "PROC_FS",
    "FHANDLE",
    "BLK_DEV_BSG",
    "NET_NS",
    "IPV6",
    "AUTOFS4_FS",
    "TMPFS_POSIX_ACL",
    "TMPFS_XATTR",
    "SECCOMP",
]

required_boot = ["squashfs"]


# class KernelPlugin(PluginV2):
class PluginImpl(PluginV2):
    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kdefconfig": {"type": "array", "default": ["defconfig"]},
                "kconfigfile": {"type": "string", "default": None},
                "kconfigflavour": {"type": "string", "default": ""},
                "kconfigs": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-image-target": {
                    "oneOf": [{"type": "string"}, {"type": "object"}],
                    "default": "",
                },
                "kernel-with-firmware": {
                    "type": "boolean",
                    "default": True,
                },
                "kernel-device-trees": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-initrd-modules": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-initrd-configured-modules": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-initrd-firmware": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-initrd-compression": {
                    "type": "string",
                    "default": "lz4",
                    "enum": ["lz4", "xz", "gz"],
                },
                "kernel-initrd-compression-options": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-initrd-channel": {
                    "type": "string",
                    "default": "stable",
                },
                "kernel-initrd-base-url": {
                    "type": "string",
                    "default": "",
                },
                "kernel-initrd-flavour": {
                    "type": "string",
                    "default": "",
                },
                "kernel-initrd-overlay": {
                    "type": "string",
                    "default": "",
                },
                "kernel-initrd-addons": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-build-efi-image": {
                    "type": "boolean",
                    "default": False,
                },
                "kernel-compiler": {
                    "type": "string",
                    "default": "",
                },
                "kernel-compiler-paths": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-compiler-parameters": {
                    "type": "array",
                    "minitems": 1,
                    "uniqueItems": True,
                    "items": {"type": "string"},
                    "default": [],
                },
                "kernel-enable-zfs-support": {
                    "type": "boolean",
                    "default": False,
                },
            },
        }

    def _init_build_env(self) -> None:
        # first get all the architectures, new v2 plugin is making life difficult
        click.echo("Initializing build env...")
        self._get_target_architecture()
        self._get_deb_architecture()
        self._get_kernel_architecture()

        self.make_cmd = ["make", "-j$(nproc)"]
        # we are building out of tree, configure paths
        self.make_cmd.append("-C")
        self.make_cmd.append("${KERNEL_SRC}")
        self.make_cmd.append("O=${SNAPCRAFT_PART_BUILD}")

        self._check_cross_compilation()
        self._set_kernel_targets()

        self.initrd_arch = self.target_arch

        # TO-DO: where do we get base?
        self.uc_series = "20"

        if (
            self.options.kernel_initrd_channel != "stable"
            and self.options.kernel_initrd_base_url
        ):
            click.echo(
                "Warning: kernel-initrd-channel and kernel-initrd-base-url "
                "are defined at the same time, kernel-initrd-channel "
                "will be ignored!!"
            )

        if (
            self.options.kernel_initrd_flavour
            and not self.options.kernel_initrd_base_url
        ):
            click.echo(
                "Warning: kernel-initrd-flavour is defined without "
                "kernel-initrd-base-url, it will be ignored!!"
            )

        if self.options.kernel_initrd_base_url:
            if self.options.kernel_initrd_flavour:
                flavour = f"-{self.options.kernel_initrd_flavour}"
            else:
                flavour = ""
        else:
            flavour = f"-{self.options.kernel_initrd_channel}"

        # determine type of initrd
        initrd_snap_file_name = _INITRD_SNAP_FILE.format(
            snap_name=_INITRD_SNAP_NAME,
            series=self.uc_series,
            flavour=flavour,
            architecture=self.initrd_arch,
        )

        self.initrd_snap_url = None
        if self.options.kernel_initrd_base_url:
            self.initrd_snap_url = _INITRD_URL.format(
                base_url=self.options.kernel_initrd_base_url,
                snap_name=initrd_snap_file_name,
            )

        self.vanilla_initrd_snap = os.path.join(
            "${SNAPCRAFT_PART_BUILD}", initrd_snap_file_name
        )

    def _get_target_architecture(self) -> None:
        # self.target_arch = os.getenv("SNAPCRAFT_TARGET_ARCH")
        # TODO: get better more reliable way to detect target arch
        # as work around check if we are cross building, to know what is
        # target arch
        self.target_arch = None
        for arg in sys.argv:
            if arg.startswith("--target-arch="):
                self.target_arch = arg.split("=")[1]

        if self.target_arch is None:
            # TDDO: there is bug in snapcraft, use uname
            # use ProjectOptions().deb_arch instead
            # self.target_arch = os.getenv("SNAP_ARCH")
            self.target_arch = ProjectOptions().deb_arch

        click.echo(f"Target architecture: {self.target_arch}")

    def _get_kernel_architecture(self) -> None:
        if self.target_arch == "armhf":
            self.kernel_arch = "arm"
        elif self.target_arch == "arm64":
            self.kernel_arch = "arm64"
        elif self.target_arch == "riscv64":
            self.kernel_arch = "riscv"
        elif self.target_arch == "amd64":
            self.kernel_arch = "x86"
        else:
            click.echo("Unknown kernel architecture!!!")

    def _get_deb_architecture(self) -> None:
        if self.target_arch == "armhf":
            self.deb_arch = "armhf"
        elif self.target_arch == "arm64":
            self.deb_arch = "arm64"
        elif self.target_arch == "riscv64":
            self.deb_arch = "riscv64"
        elif self.target_arch == "amd64":
            self.deb_arch = "amd64"
        else:
            click.echo("Unknown deb architecture!!!")

    def _check_cross_compilation(self) -> None:
        host_arch = os.getenv("SNAP_ARCH")
        if host_arch != self.target_arch:
            click.echo(f"Configuring cross build to {self.kernel_arch}")
            self.make_cmd.append(f"ARCH={self.kernel_arch}")
            self.make_cmd.append("CROSS_COMPILE=${SNAPCRAFT_ARCH_TRIPLET}-")

    def _set_kernel_targets(self) -> None:
        if not self.options.kernel_image_target:
            self.kernel_image_target = default_kernel_image_target[self.deb_arch]
        elif isinstance(self.options.kernel_image_target, str):
            self.kernel_image_target = self.options.kernel_image_target
        elif self.deb_arch in self.options.kernel_image_target:
            self.kernel_image_target = self.options.kernel_image_target[self.deb_arch]

        self.make_targets = [self.kernel_image_target, "modules"]
        self.make_install_targets = [
            "modules_install",
            "INSTALL_MOD_STRIP=1",
            "INSTALL_MOD_PATH=${SNAPCRAFT_PART_INSTALL}",
        ]
        self.dtbs = [f"{i}.dtb" for i in self.options.kernel_device_trees]
        if self.dtbs:
            self.make_targets.extend(self.dtbs)
        elif self.kernel_arch in ("arm", "arm64", "riscv64"):
            self.make_targets.append("dtbs")
            self.make_install_targets.extend(
                ["dtbs_install", "INSTALL_DTBS_PATH=${SNAPCRAFT_PART_INSTALL}/dtbs"]
            )
        self.make_install_targets.extend(self._get_fw_install_targets())

    def _get_fw_install_targets(self) -> List[str]:
        if not self.options.kernel_with_firmware:
            return []

        return [
            "firmware_install",
            "INSTALL_FW_PATH=${SNAPCRAFT_PART_INSTALL}/lib/firmware",
        ]

    def _link_files_fnc_cmd(self) -> List[str]:
        return [
            " ".join(["# link files, accept wild cards"]),
            " ".join(
                ["# 1: reference dir, 2: file(s) including wild cards, 3: dst dir"]
            ),
            " ".join(["link_files() {"]),
            " ".join(["\tif [ -d ${1}/${2} ]; then"]),
            " ".join(["\t\tfor f in $(ls ${1}/${2})"]),
            " ".join(["\t\tdo"]),
            " ".join(["\t\t\tlink_files ${1} ${2}/${f} ${3}"]),
            " ".join(["\t\tdone"]),
            " ".join(["\t\treturn 0"]),
            " ".join(["\tfi"]),
            " ".join([""]),
            " ".join(['\tlocal found=""']),
            " ".join(["\tfor f in $(ls ${1}/${2})"]),
            " ".join(["\tdo"]),
            " ".join(['\t\tif [[ -L "${f}" ]]; then']),
            " ".join(
                [
                    "\t\t\tlocal rel_path=$(",
                    "realpath",
                    "--no-symlinks",
                    "--relative-to=${1}",
                    "${f}",
                    ")",
                ]
            ),
            " ".join(["\t\telse"]),
            " ".join(
                [
                    "\t\t\tlocal rel_path=$(",
                    "realpath",
                    "-se",
                    "--relative-to=${1}",
                    "${f}",
                    ")",
                ]
            ),
            " ".join(["\t\tfi"]),
            " ".join(["\t\tlocal dir_path=$(dirname ${rel_path})"]),
            " ".join(["\t\tmkdir -p ${3}/${dir_path}"]),
            " ".join(['\t\techo "installing ${f} to ${3}/${dir_path}"']),
            " ".join(["\t\tln -f ${f} ${3}/${dir_path}"]),
            " ".join(['\t\tfound="yes"']),
            " ".join(["\tdone"]),
            " ".join(['\tif [ "yes" = "${found}" ]; then']),
            " ".join(["\t\treturn 0"]),
            " ".join(["\telse"]),
            " ".join(["\t\treturn 1"]),
            " ".join(["\tfi"]),
            " ".join(["}"]),
        ]

    def _download_generic_initrd_cmd(self) -> List[str]:
        # we can have url or snap name with channel/track/arch
        if self.initrd_snap_url:
            cmd_download_initrd = [
                " ".join(['\techo "Downloading vanilla initrd from custom url"']),
                " ".join(
                    [
                        "\tcurl",
                        "-f",
                        "-o",
                        f'"{self.vanilla_initrd_snap}"',
                        f'"{self.initrd_snap_url}"',
                    ]
                ),
            ]
        else:
            cmd_download_initrd = [
                " ".join(['\techo "Downloading vanilla initrd from snap store"']),
                " ".join(
                    [
                        f"UBUNTU_STORE_ARCH={self.initrd_arch}",
                        "snap",
                        "download",
                        "uc-initrd",
                        "--channel",
                        f"{self.uc_series}/{self.options.kernel_initrd_channel}",
                        "--basename",
                        f"$(basename {self.vanilla_initrd_snap} | cut -f1 -d'.')",
                    ]
                ),
            ]

        return [
            " ".join(['echo "Geting generic initrd snap..."']),
            # only download again if files does not exist, otherwise
            # assume we are re-running build
            " ".join(
                [
                    f"if [ ! -e {self.vanilla_initrd_snap} ]; then",
                ]
            ),
            *cmd_download_initrd,
            " ".join(["fi"]),
        ]

    def _clone_zfs_cmd(self) -> List[str]:
        # clone zfs if needed
        if self.options.kernel_enable_zfs_support:
            return [
                " ".join(["if [ ! -d ${SNAPCRAFT_PART_BUILD}/zfs ]; then"]),
                " ".join(['\techo "clonning zfs..."']),
                " ".join(
                    [
                        "\tgit",
                        "clone",
                        "--depth=1",
                        _ZFS_URL,
                        "${SNAPCRAFT_PART_BUILD}/zfs",
                        "-b",
                        "master",
                    ]
                ),
                " ".join(["fi"]),
            ]
        return [
            " ".join(['echo "zfs is not enabled"']),
        ]

    def _unpack_generic_initrd_cmd(self) -> List[str]:
        cmd_rm = [
            "[ -e ${INITRD_STAGING} ]",
            "&&",
            "rm",
            "-rf",
            "${INITRD_STAGING}",
        ]
        cmd_mkdir = [
            "mkdir",
            "-p",
            "${INITRD_STAGING}",
        ]
        cmd_unsquash = [
            "unsquashfs",
            "-f",
            "-d",
            "${INITRD_UNPACKED_SNAP}",
            f'"{self.vanilla_initrd_snap}"',
        ]

        tmp_initrd_path = "${INITRD_UNPACKED_SNAP}/initrd.img"
        cmd_uncompress_initrd = [
            "unmkinitramfs",
            f'"{tmp_initrd_path}"',
            "${INITRD_STAGING}",
        ]

        return [
            " ".join(['echo "Unpack vanilla initrd..."']),
            " ".join(cmd_rm),
            " ".join(cmd_mkdir),
            " ".join(cmd_unsquash),
            " ".join(cmd_uncompress_initrd),
        ]

    def _make_initrd_cmd(self) -> List[str]:

        cmd_echo = [
            " ".join(
                [
                    "echo",
                    '"Generating initrd with ko modules for kernel release: ${KERNEL_RELEASE}"',
                ]
            ),
        ]

        # For x86 we could have 'early' (microcode updates) and 'main'
        # segments, we modify the latter.
        cmd_get_initrd_unpacked_path = [
            " ".join(["if [ -d ${INITRD_STAGING}/main ]; then"]),
            " ".join(["\tinitrd_unpacked_path_main=${INITRD_STAGING}/main"]),
            " ".join(["else"]),
            " ".join(["\tinitrd_unpacked_path_main=${INITRD_STAGING}"]),
            " ".join(["fi"]),
        ]

        cmd_install_modules = [
            # install required modules to initrd
            " ".join(['echo "Installing ko modules to initrd..."']),
            " ".join(['install_modules=""']),
            " ".join(['echo "Gathering module dependencies..."']),
            " ".join(
                [
                    "for",
                    "m",
                    "in",
                    f"{' '.join(self.options.kernel_initrd_modules)} {' '.join(self.options.kernel_initrd_configured_modules)}",
                ]
            ),
            " ".join(["do"]),
            " ".join(
                [
                    '\tinstall_modules="${install_modules}',
                    "$(modprobe",
                    "-n",
                    "-q",
                    "--show-depends",
                    "-d",
                    '"${SNAPCRAFT_PART_INSTALL}"',
                    "-S",
                    '"${KERNEL_RELEASE}"',
                    "${m}",
                    "|",
                    "awk",
                    "'{ if ($1 != \"builtin\") print $2;}'",
                    ')"',
                ]
            ),
            " ".join(["done"]),
        ]

        cmd_install_modules.extend(
            [
                " ".join([""]),
                " ".join(['echo "Installing modules: ${install_modules}"']),
                " ".join(
                    ["for m in $(echo ${install_modules} | tr ' ' '\\n' | sort | uniq)"]
                ),
                " ".join(["do"]),
                " ".join(
                    [
                        "\tlink_files",
                        "${SNAPCRAFT_PART_INSTALL}",
                        "$(realpath",
                        "--relative-to=${SNAPCRAFT_PART_INSTALL}",
                        "${m})",
                        "${initrd_unpacked_path_main}",
                    ]
                ),
                " ".join(["done"]),
                " ".join([""]),
            ]
        )

        cmd_install_modules.extend(
            [
                " ".join(['echo "Rebuild modules dep list in initrd..."']),
                " ".join(
                    [
                        "if [ -d ${initrd_unpacked_path_main}/lib/modules/${KERNEL_RELEASE} ]; then"
                    ]
                ),
                " ".join(
                    [
                        "\tcp ${SNAPCRAFT_PART_INSTALL}/lib/modules/${KERNEL_RELEASE}/modules.order ${initrd_unpacked_path_main}/lib/modules/${KERNEL_RELEASE}"
                    ]
                ),
                " ".join(
                    [
                        "\tcp ${SNAPCRAFT_PART_INSTALL}/lib/modules/${KERNEL_RELEASE}/modules.builtin ${initrd_unpacked_path_main}/lib/modules/${KERNEL_RELEASE}"
                    ]
                ),
                " ".join(
                    [
                        "\tdepmod",
                        "-b",
                        "${initrd_unpacked_path_main}",
                        "${KERNEL_RELEASE}",
                    ]
                ),
                " ".join(["fi"]),
                " ".join([""]),
            ]
        )

        cmd_install_modules.extend(
            [
                " ".join(
                    [
                        'echo "Configuring ubuntu-core-initramfs.conf with supported modules"'
                    ]
                ),
                " ".join(
                    ['echo "If modules is not included in initrd, do not include it"']
                ),
                " ".join(
                    [
                        "initramfs_conf=${initrd_unpacked_path_main}/usr/lib/modules-load.d/ubuntu-core-initramfs.conf"
                    ]
                ),
                " ".join(['echo "# configures modules" > ${initramfs_conf}']),
                " ".join(
                    [
                        f"for m in {' '.join(self.options.kernel_initrd_configured_modules)}",
                    ]
                ),
                " ".join(["do"]),
                " ".join(
                    [
                        "\tif [",
                        "-n",
                        '"$(modprobe -n -q --show-depends -d ${initrd_unpacked_path_main} -S "${KERNEL_RELEASE}" ${m})"',
                        "]; then",
                    ]
                ),
                " ".join(["\t\techo ${m} >> ${initramfs_conf}"]),
                " ".join(["\tfi"]),
                " ".join(["done"]),
            ]
        )

        # gather firmware files
        cmd_copy_initrd_overlay = [
            " ".join(['echo "Installing initrd overlay..."']),
            " ".join(
                [f"for f in {' '.join(self.options.kernel_initrd_firmware)}"]
            ),
            " ".join(["do"]),
            # firmware can be from kernel build or from stage
            # firmware from kernel build takes preference
            " ".join(
                [
                    "\tif !",
                    "link_files",
                    "${SNAPCRAFT_PART_INSTALL}",
                    "${f}",
                    "${initrd_unpacked_path_main}/lib",
                    ";",
                    "then",
                ]
            ),
            " ".join(
                [
                    "\t\tif !",
                    "link_files",
                    "${SNAPCRAFT_STAGE}",
                    "${f}",
                    "${initrd_unpacked_path_main}/lib",
                    ";",
                    "then",
                ]
            ),
            " ".join(['\t\t\techo "Missing firmware [${f}], ignoring it"']),
            " ".join(["\t\tfi"]),
            " ".join(["\tfi"]),
            " ".join(["done"]),
        ]

        # apply overlay if defined
        if self.options.kernel_initrd_overlay:
            cmd_copy_initrd_overlay.extend(
                [
                    " ".join(
                        [
                            "link_files",
                            "${SNAPCRAFT_STAGE}",
                            f"{self.options.kernel_initrd_overlay}",
                            "${initrd_unpacked_path_main}",
                        ]
                    ),
                ]
            )

        # apply overlay addons if defined
        cmd_copy_initrd_overlay.extend(
            [
                " ".join([""]),
                " ".join(['echo "Installing initrd addons..."']),
                " ".join(
                    [f"for a in {' '.join(self.options.kernel_initrd_addons)}"]
                ),
                " ".join(["do"]),
                " ".join(
                    [
                        "\techo",
                        '"Copy overlay: ${a}"',
                    ]
                ),
                " ".join(
                    [
                        "\tlink_files",
                        "${SNAPCRAFT_STAGE}",
                        "${a}",
                        "${initrd_unpacked_path_main}",
                    ]
                ),
                " ".join(["done"]),
            ],
        )

        cmd_pack_initrd = [
            " ".join(
                [
                    "if compgen -G  ${SNAPCRAFT_PART_INSTALL}/initrd.img* > ",
                    "/dev/null; then",
                ]
            ),
            " ".join(["\trm -rf ${SNAPCRAFT_PART_INSTALL}/initrd.img*"]),
            " ".join(["fi"]),
        ]

        cmd_pack_initrd.extend(
            [
                " ".join([""]),
                " ".join(["if [ -d ${INITRD_STAGING}/early ]; then"]),
                " ".join(["\tcd ${INITRD_STAGING}/early"]),
                " ".join(
                    [
                        "\tfind . | cpio --create --format=newc --owner=0:0 > ",
                        "${SNAPCRAFT_PART_INSTALL}/initrd.img-${KERNEL_RELEASE}",
                    ]
                ),
                " ".join(["fi"]),
            ]
        )

        cmd_pack_initrd.extend(
            [
                " ".join([""]),
                " ".join(["cd", "${initrd_unpacked_path_main}"]),
                " ".join(
                    [
                        "find . | cpio --create --format=newc --owner=0:0 | ",
                        f"{self._compression_cmd()} >> ",
                        "${SNAPCRAFT_PART_INSTALL}/initrd.img-${KERNEL_RELEASE}",
                    ]
                ),
                " ".join([""]),
                " ".join(['echo "Installing new initrd.img..."']),
                " ".join(
                    [
                        "ln -f",
                        "${SNAPCRAFT_PART_INSTALL}/initrd.img-${KERNEL_RELEASE}",
                        "${SNAPCRAFT_PART_INSTALL}/initrd.img",
                    ]
                ),
            ]
        )

        return [
            *cmd_echo,
            *self._unpack_generic_initrd_cmd(),
            " ".join([""]),
            *cmd_get_initrd_unpacked_path,
            " ".join(["\n"]),
            *cmd_install_modules,
            " ".join([""]),
            *cmd_copy_initrd_overlay,
            " ".join([""]),
            " ".join(['echo "Pack new initrd..."']),
            *cmd_pack_initrd,
        ]

    def _compression_cmd(self) -> str:
        compressor = _compression_command[self.options.kernel_initrd_compression]
        options = ""
        if self.options.kernel_initrd_compression_options:
            for opt in self.options.kernel_initrd_compression_options:
                options = f"{options} {opt}"
        else:
            options = _compressor_options[self.options.kernel_initrd_compression]

        cmd = f"{compressor} {options}"
        click.echo(f"Using initrd compressions command: {cmd!r}")
        return cmd

    def _parse_kernel_release_cmd(self) -> List[str]:
        return [
            " ".join(['echo "Parsing created kernel release..."']),
            " ".join(
                [
                    "KERNEL_RELEASE=$(cat ${SNAPCRAFT_PART_BUILD}/include/config/kernel.release)",
                ]
            ),
        ]

    def _copy_vmlinuz_cmd(self) -> List[str]:
        cmd = [
            " ".join(['echo "Copying kernel image..."']),
            # if kernel already exists, replace it, we are probably re-runing
            # build
            " ".join(
                [
                    "[ -e ${SNAPCRAFT_PART_INSTALL}/kernel.img ]",
                    "&&",
                    "rm -rf ${SNAPCRAFT_PART_INSTALL}/kernel.img",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${KERNEL_BUILD_ARCH_DIR}/${KERNEL_IMAGE_TARGET}",
                    "${SNAPCRAFT_PART_INSTALL}/${KERNEL_IMAGE_TARGET}-${KERNEL_RELEASE}",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${KERNEL_BUILD_ARCH_DIR}/${KERNEL_IMAGE_TARGET}",
                    "${SNAPCRAFT_PART_INSTALL}/kernel.img",
                ]
            ),
        ]
        return cmd

    def _copy_system_map_cmd(self) -> List[str]:
        cmd = [
            " ".join(['echo "Copying System map..."']),
            " ".join(
                [
                    "[ -e ${SNAPCRAFT_PART_INSTALL}/System.map ]",
                    "&&",
                    "rm -rf ${SNAPCRAFT_PART_INSTALL}/System.map*",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_BUILD}/System.map",
                    "${SNAPCRAFT_PART_INSTALL}/System.map-${KERNEL_RELEASE}",
                ]
            ),
        ]
        return cmd

    def _copy_dtbs_cmd(self) -> List[str]:
        if not self.options.kernel_device_trees:
            return [" ".join([""])]

        cmd = [
            " ".join(['echo "Copying custom dtbs..."']),
            " ".join(["mkdir -p ${SNAPCRAFT_PART_INSTALL}/dtbs"]),
        ]
        for dtb in self.dtbs:
            # Strip any subdirectories
            subdir_index = dtb.rfind("/")
            if subdir_index > 0:
                install_dtb = dtb[subdir_index + 1:]
            else:
                install_dtb = dtb

            cmd.extend(
                [
                    " ".join(
                        [
                            "ln -f",
                            f"${{KERNEL_BUILD_ARCH_DIR}}/dts/{dtb}",
                            f"${{SNAPCRAFT_PART_INSTALL}}/dtbs/{install_dtb}",
                        ]
                    ),
                ]
            )
        return cmd

    def _assemble_ubuntu_config_cmd(self) -> List[str]:
        flavour = self.options.kconfigflavour
        click.echo(f"Using ubuntu config flavour {flavour}")
        cmd = [
            " ".join(['\techo "Assembling Ubuntu config..."']),
            " ".join(
                [
                    "\t"
                    "branch=$(cut -d'.' -f 2- < ${KERNEL_SRC}/debian/debian.env)",
                ]
            ),
            " ".join(["\tbaseconfigdir=${KERNEL_SRC}/debian.${branch}/config"]),
            " ".join(
                [
                    "\tarchconfigdir=${KERNEL_SRC}/debian.${branch}/config/${DEB_ARCH}"
                ]
            ),
            " ".join(["\tcommonconfig=${baseconfigdir}/config.common.ports"]),
            " ".join(["\tubuntuconfig=${baseconfigdir}/config.common.ubuntu"]),
            " ".join(["\tarchconfig=${archconfigdir}/config.common.${DEB_ARCH}"]),
            " ".join(
                [f"\tflavourconfig=${{archconfigdir}}/config.flavour.{flavour}"]
            ),
            " ".join(
                [
                    "\tcat",
                    "${commonconfig}",
                    "${ubuntuconfig}",
                    "${archconfig}",
                    "${flavourconfig}",
                    ">",
                    "${SNAPCRAFT_PART_BUILD}/.config",
                ]
            ),
        ]
        return cmd

    def _do_base_config_cmd(self) -> List[str]:
        # if the parts build dir already contains a .config file,
        # use it
        cmd = [
            " ".join(['echo "Preparing config..."']),
            " ".join(["if [ ! -e ${SNAPCRAFT_PART_BUILD}/.config ]; then"]),
        ]

        # if kconfigfile is provided use that
        # elif kconfigflavour is provided, assemble the ubuntu.flavour config
        # otherwise use defconfig to seed the base config
        if self.options.kconfigfile:
            cmd.extend(
                [
                    " ".join(
                        [
                            "\t",
                            "cp",
                            f"{self.options.kconfigfile}",
                            "${SNAPCRAFT_PART_BUILD}/.config",
                        ]
                    ),
                ],
            )
        elif self.options.kconfigflavour:
            cmd.extend(self._assemble_ubuntu_config_cmd())
        else:
            # we need to run this with -j1, unit tests are a good defense here.
            make_cmd = self.make_cmd.copy()
            make_cmd[1] = "-j1"
            cmd.extend(
                [
                    " ".join(
                        [
                            "\t",
                            " ".join(make_cmd),
                            " ".join(self.options.kdefconfig),
                        ]
                    ),
                ]
            )
        # close if statement
        cmd.extend([" ".join(["fi"])])
        return cmd

    def _do_patch_config_cmd(self) -> List[str]:
        # prepend the generated file with provided kconfigs
        #  - concat kconfigs to buffer
        #  - read current .config and append
        #  - write out to disk
        if not self.options.kconfigs:
            return [" ".join([])]

        config = "\n".join(self.options.kconfigs)

        # note that prepending and appending the overrides seems
        # only way to convince all kbuild versions to pick up the
        # configs during oldconfig in .config
        return [
            " ".join(['echo "Appling extra config...."']),
            " ".join(
                [
                    f"echo '{config}'",
                    ">",
                    "${SNAPCRAFT_PART_BUILD}/.config_snap",
                ]
            ),
            " ".join(
                [
                    "cat",
                    "${SNAPCRAFT_PART_BUILD}/.config",
                    ">>",
                    "${SNAPCRAFT_PART_BUILD}/.config_snap",
                ]
            ),
            " ".join(
                [
                    f"echo '{config}'",
                    ">>",
                    "${SNAPCRAFT_PART_BUILD}/.config_snap",
                ]
            ),
            " ".join(
                [
                    "mv",
                    "${SNAPCRAFT_PART_BUILD}/.config_snap",
                    "${SNAPCRAFT_PART_BUILD}/.config",
                ]
            ),
        ]

    def _do_remake_config_cmd(self) -> List[str]:
        # update config to include kconfig amendments using oldconfig
        make_cmd = self.make_cmd.copy()
        make_cmd[1] = "-j1"
        return [
            " ".join(['echo "Remaking oldconfig...."']),
            " ".join(
                [
                    "bash -c ' yes \"\"",
                    "|| true'",
                    f"| {' '.join(make_cmd)} oldconfig",
                ]
            ),
        ]

    def _get_configure_command(self) -> List[str]:
        return [
            *self._do_base_config_cmd(),
            " ".join(["\n"]),
            *self._do_patch_config_cmd(),
            " ".join(["\n"]),
            *self._do_remake_config_cmd(),
        ]

    def _call_check_config_cmd(self) -> List[str]:
        return [
            " ".join(['echo "Checking config for expected options..."']),
            " ".join(
                [
                    sys.executable,
                    "-I",
                    os.path.abspath(__file__),
                    "check-new-config",
                    "--config-path",
                    "${SNAPCRAFT_PART_BUILD}/.config",
                ]
            ),
        ]

    def check_new_config(self, config_path: str):
        click.echo("Checking created config...")
        builtin, modules = self._do_parse_config(config_path)
        self._do_check_config(builtin, modules)
        self._do_check_initrd(builtin, modules)

    def _do_parse_config(self, config_path: str):
        builtin = []
        modules = []
        # tokenize .config and store options in builtin[] or modules[]
        with open(config_path, encoding="utf8") as f:
            for line in f:
                tok = line.strip().split("=")
                items = len(tok)
                if items == 2:
                    opt = tok[0].upper()
                    val = tok[1].upper()
                    if val == "Y":
                        builtin.append(opt)
                    elif val == "M":
                        modules.append(opt)
        return builtin, modules

    def _do_check_config(self, builtin: List[str], modules: List[str]):
        # check the resulting .config has all the necessary options
        msg = (
            "**** WARNING **** WARNING **** WARNING **** WARNING ****\n"
            "Your kernel config is missing some features that Ubuntu Core "
            "recommends or requires.\n"
            "While we will not prevent you from building this kernel snap, "
            "we suggest you take a look at these:\n"
        )
        required_opts = (
            required_generic + required_security + required_snappy + required_systemd
        )
        missing = []

        for code in required_opts:
            opt = f"CONFIG_{code}"
            if opt in builtin or opt in modules:
                continue
            missing.append(opt)

        if missing:
            warn = f"\n{msg}\n"
            for opt in missing:
                note = ""
                if opt == "CONFIG_CC_STACKPROTECTOR_STRONG":
                    note = "(4.1.x and later versions only)"
                elif opt == "CONFIG_DEVPTS_MULTIPLE_INSTANCES":
                    note = "(4.8.x and earlier versions only)"
                warn += f"{opt} {note}\n"
            click.echo(warn)

    def _do_check_initrd(self, builtin: List[str], modules: List[str]):
        # check all required_boot[] items are either builtin or part of initrd
        msg = (
            "**** WARNING **** WARNING **** WARNING **** WARNING ****\n"
            "The following features are deemed boot essential for\n"
            "ubuntu core, consider making them static[=Y] or adding\n"
            "the corresponding module to initrd:\n"
        )
        missing = []

        for code in required_boot:
            opt = f"CONFIG_{code.upper()}"
            if opt in builtin:
                continue
            if opt in modules and code in self.options.kernel_initrd_modules:
                continue
            missing.append(opt)

        if missing:
            warn = f"\n{msg}\n"
            for opt in missing:
                warn += f"{opt}\n"
            click.echo(warn)

    def _clean_old_build_cmd(self) -> List[str]:
        return [
            " ".join([""]),
            " ".join(['echo "Cleaning previous build first..."']),
            " ".join(
                [
                    "[ -e ${SNAPCRAFT_PART_INSTALL}/modules ]",
                    "&&",
                    "rm -rf ${SNAPCRAFT_PART_INSTALL}/modules",
                ]
            ),
            " ".join(
                [
                    "[ -L ${SNAPCRAFT_PART_INSTALL}/lib/modules ]",
                    "&&",
                    "rm -rf ${SNAPCRAFT_PART_INSTALL}/lib/modules",
                ]
            ),
        ]

    def _arrange_install_dir_cmd(self) -> List[str]:
        return [
            " ".join([""]),
            " ".join(['echo "Finalizing install directory..."']),
            # upstream kernel installs under $INSTALL_MOD_PATH/lib/modules/
            # but snapd expects modules/ and firmware/
            " ".join(
                [
                    "mv",
                    "${SNAPCRAFT_PART_INSTALL}/lib/modules",
                    "${SNAPCRAFT_PART_INSTALL}/",
                ]
            ),
            # remove sym links modules/*/build and modules/*/source
            " ".join(
                [
                    "rm",
                    "${SNAPCRAFT_PART_INSTALL}/modules/*/build",
                    "${SNAPCRAFT_PART_INSTALL}/modules/*/source",
                ]
            ),
            # if there is firmware dir, move it to snap root
            # this could have been from stage packages or from kernel build
            " ".join(
                [
                    "[ -d ${SNAPCRAFT_PART_INSTALL}/lib/firmware ]",
                    "&&",
                    "mv",
                    "${SNAPCRAFT_PART_INSTALL}/lib/firmware",
                    "${SNAPCRAFT_PART_INSTALL}",
                ]
            ),
            # create sym links for modules and firmware for convenience
            " ".join(
                [
                    "ln",
                    "-sf",
                    "../modules",
                    "${SNAPCRAFT_PART_INSTALL}/lib/modules",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-sf",
                    "../firmware",
                    "${SNAPCRAFT_PART_INSTALL}/lib/firmware",
                ]
            ),
        ]

    def _install_config_cmd(self) -> List[str]:
        # install .config as config-$version
        return [
            " ".join([""]),
            " ".join(['echo "Installing kernel config..."']),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_BUILD}/.config",
                    "${SNAPCRAFT_PART_INSTALL}/config-${KERNEL_RELEASE}",
                ]
            ),
        ]

    def _configure_compiler(self) -> None:
        # check if we gcc or another compiler
        if self.options.kernel_compiler:
            # at the moment only clang is supported as alternative, warn otherwise
            if self.options.kernel_compiler != "clang":
                click.echo("Only other 'supported' compiler is clang")
                click.echo("hopefully you know what you are doing")
            self.make_cmd.append(f"CC=\"{self.options.kernel_compiler}\"")
        if self.options.kernel_compiler_parameters:
            for opt in self.options.kernel_compiler_parameters:
                self.make_cmd.append(str(opt))

    def _make_efi_cmd(self) -> List[str]:
        # kernel_f = "${KERNEL_IMAGE_TARGET}-${KERNEL_RELEASE}"
        kernel_p = "${SNAPCRAFT_PART_INSTALL}/${KERNEL_IMAGE_TARGET}-${KERNEL_RELEASE}"
        initrd_p = "${SNAPCRAFT_PART_INSTALL}/initrd.img"
        efi_img_p = "${SNAPCRAFT_PART_INSTALL}/kernel.efi"
        arch = {"amd64": "x64", "arm64": "aa64"}.get(self.deb_arch)
        return [
            " ".join(['echo "Building efi image..."']),
            " ".join(
                [
                    "objcopy",
                    "--add-section",
                    f".linux={kernel_p}",
                    "--change-section-vma",
                    ".linux=0x40000",
                    "--add-section",
                    f".initrd={initrd_p}",
                    "--change-section-vma",
                    ".initrd=0x3000000",
                    f"/usr/lib/systemd/boot/efi/linux{arch}.efi.stub",
                    efi_img_p,
                ]
            ),
        ]

    def get_build_snaps(self) -> Set[str]:
        return set()

    def get_build_packages(self) -> Set[str]:
        build_packages = {
            "bc",
            "gcc",
            "cmake",
            "kmod",
            "xz-utils",
            "initramfs-tools-core",
            "systemd",
            "lz4",
            "curl",
        }
        if self.options.kernel_enable_zfs_support:
            build_packages |= {
                "autoconf",
                "automake",
                "libblkid-dev",
                "libtool",
                "python3",
            }
        return build_packages

    def get_build_environment(self) -> Dict[str, str]:
        click.echo("Getting build env...")
        self._init_build_env()

        env = {
            "CROSS_COMPILE": "${SNAPCRAFT_ARCH_TRIPLET}-",
            "ARCH": self.kernel_arch,
            "DEB_ARCH": "${SNAPCRAFT_TARGET_ARCH}",
            "INITRD_STAGING": "${SNAPCRAFT_PART_BUILD}/initrd-staging",
            "INITRD_UNPACKED_SNAP": "${SNAPCRAFT_PART_BUILD}/unpacked_snap",
            "KERNEL_BUILD_ARCH_DIR": "${SNAPCRAFT_PART_BUILD}/arch/${ARCH}/boot",
            "KERNEL_IMAGE_TARGET": self.kernel_image_target,
        }

        # check if there is custom path to be included
        if self.options.kernel_compiler_paths:
            for p in self.options.kernel_compiler_paths:
                custom_path = "{}{}:".format(
                    os.path.join("${SNAPCRAFT_STAGE}", p), custom_path
                )
            env["PATH"] = (f"{self.custom_path}:$PATH")

        if "MAKEFLAGS" in os.environ:
            makeflags = re.sub(r"-I[\S]*", "", os.environ["MAKEFLAGS"])
            env["MAKEFLAGS"] = makeflags

        return env

    def _get_build_command(self) -> List[str]:
        return [
            " ".join(['echo "Building kernel..."']),
            " ".join(self.make_cmd + self.make_targets),
        ]

    def _get_post_install_cmd(self) -> List[str]:
        return [
            " ".join(["\n"]),
            *self._parse_kernel_release_cmd(),
            " ".join(["\n"]),
            *self._copy_vmlinuz_cmd(),
            " ".join([""]),
            *self._copy_system_map_cmd(),
            " ".join([""]),
            *self._copy_dtbs_cmd(),
            " ".join([""]),
            *self._make_initrd_cmd(),
            " ".join([""]),
        ]

    def _get_install_command(self) -> List[str]:
        # install to installdir
        cmd = [
            " ".join(['echo "Installing kernel build..."']),
            " ".join(
                self.make_cmd
                + ["CONFIG_PREFIX=${SNAPCRAFT_PART_INSTALL}"]
                + self.make_install_targets
            ),
        ]

        # add post install steps
        cmd.extend(
            self._get_post_install_cmd(),
        )

        # create kernel.efi if requested
        if self.options.kernel_build_efi_image:
            cmd.extend(self._make_efi_cmd())

        # install .config as config-$version
        cmd.extend(self._install_config_cmd())

        cmd.extend(self._arrange_install_dir_cmd())

        return cmd

    def _get_zfs_build_commands(self) -> List[str]:
        # include zfs build steps if required
        if self.options.kernel_enable_zfs_support:
            return [
                " ".join(['echo "Building zfs modules..."']),
                " ".join(
                    [
                        "cd",
                        "${SNAPCRAFT_PART_BUILD}/zfs",
                    ]
                ),
                " ".join(["./autogen.sh"]),
                " ".join(
                    [
                        "./configure",
                        "--with-linux=${KERNEL_SRC}",
                        "--with-linux-obj=${SNAPCRAFT_PART_BUILD}",
                        "--with-config=kernel",
                    ]
                ),
                " ".join(["make -j$(nproc)"]),
                " ".join(
                    [
                        "make",
                        "install",
                        "DESTDIR=${SNAPCRAFT_PART_INSTALL}/zfs",
                    ]
                ),
                " ".join(['release_version="$(ls ${SNAPCRAFT_PART_INSTALL}/modules)"']),
                " ".join(
                    [
                        "mv",
                        "${SNAPCRAFT_PART_INSTALL}/zfs/lib/modules/${release_version}/extra",
                        "${SNAPCRAFT_PART_INSTALL}/modules/${release_version}",
                    ]
                ),
                " ".join(
                    [
                        "rm",
                        "-rf",
                        "${SNAPCRAFT_PART_INSTALL}/zfs",
                    ]
                ),
                " ".join(['echo "Rebuilding module dependencies"']),
                " ".join(["depmod -b ${SNAPCRAFT_PART_INSTALL} ${release_version}"]),
            ]
        return [
            " ".join(['echo "Not building zfs modules"']),
        ]

    def get_build_commands(self) -> List[str]:
        click.echo("Getting build commands...")
        self._configure_compiler()
        # kernel source can be either SNAPCRAFT_PART_SRC or SNAPCRAFT_PROJECT_DIR
        return [
            " ".join(['[ -d ${SNAPCRAFT_PART_SRC}/kernel ] && KERNEL_SRC=${SNAPCRAFT_PART_SRC} || KERNEL_SRC=${SNAPCRAFT_PROJECT_DIR}']),
            " ".join(['echo "PATH=$PATH"']),
            " ".join(['echo "KERNEL_SRC=${KERNEL_SRC}"']),
            " ".join([""]),
            *self._link_files_fnc_cmd(),
            " ".join([""]),
            *self._download_generic_initrd_cmd(),
            " ".join([""]),
            *self._clone_zfs_cmd(),
            " ".join([""]),
            *self._clean_old_build_cmd(),
            " ".join(["\n"]),
            *self._get_configure_command(),
            # " ".join(["\n"]),
            # *self._call_check_config_cmd(),
            " ".join(["\n"]),
            *self._get_build_command(),
            " ".join(["\n"]),
            *self._get_install_command(),
            " ".join(["\n"]),
            *self._get_zfs_build_commands(),
            " ".join(["\n"]),
            " ".join(['echo "Kernel build finished!"']),
        ]

    @property
    def out_of_source_build(self):
        # user src dir without need to link it to build dir, which takes ages
        return True
