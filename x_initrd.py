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

"""The initrd plugin allows building kernel snaps
with all the bells and whistles in one shot...


The following initrd specific options are provided by this plugin:
    - kernel-image-target:
      (yaml object, string or null for default target)
      the default target is bzImage and can be set to any specific
      target.
      For more complex cases where one would want to use
      the same snapcraft.yaml to target multiple architectures a
      yaml object can be used. This yaml object would be a map of
      debian architecture and kernel image build targets.

    - kernel-build-efi-image
      Optional, true if we want to create an EFI image, false otherwise (false
      by default)

      Expected kernel image is then:
         <install dir>/boot/<kernel-image-target>-<kernel release>

    - kernel-initrd-modules:
      (array of string)
      list of modules to include in initrd; note that kernel snaps do not
      provide the core boot logic which comes from snappy Ubuntu Core
      OS snap. Include all modules you need for mounting rootfs here.

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
"""

import click
import logging
import os
import sys

from snapcraft import ProjectOptions
from typing import Any, Dict, List, Set

from snapcraft.plugins.v2 import PluginV2

_compression_command = {"gz": "gzip", "lz4": "lz4", "xz": "xz"}
_compressor_options = {"gz": "-7", "lz4": "-l -9", "xz": "-7"}
_INITRD_URL = "{base_url}/{snap_name}"
_INITRD_SNAP_NAME = "uc-initrd"
_INITRD_SNAP_FILE = "{snap_name}_{series}{flavour}_{architecture}.snap"

default_kernel_image_target = {
    "amd64": "bzImage",
    "i386": "bzImage",
    "armhf": "vmlinuz",
    "arm64": "vmlinuz",
    "powerpc": "uImage",
    "ppc64el": "vmlinux.strip",
    "s390x": "bzImage",
}

# class KernelPlugin(PluginV2):
class PluginImpl(PluginV2):
    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        return {
            "$schema": "http://json-schema.org/draft-04/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "kernel-image-target": {
                    "oneOf": [{"type": "string"}, {"type": "object"}],
                    "default": "",
                },
                "kernel-initrd-modules": {
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
            },
        }

    def _init_build_env(self) -> None:
        # first get all the architectures, new v2 plugin is making life difficult
        click.echo("Initializing build env...")
        self._get_target_architecture()
        self._get_deb_architecture()
        self._get_kernel_architecture()

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
                "Warning: kernel-initrd-flavour is defined withut "
                "kernel-initrd-base-url, it will be ignored!!"
            )

        if self.options.kernel_initrd_base_url:
            if self.options.kernel_initrd_flavour:
                flavour = "-{}".format(self.options.kernel_initrd_flavour)
            else:
                flavour = ""
        else:
            flavour = "-{}".format(self.options.kernel_initrd_channel)

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

        click.echo("Target architecture: {}".format(self.target_arch))

    def _get_kernel_architecture(self) -> None:
        if self.target_arch == "armhf":
            self.kernel_arch = "arm"
        elif self.target_arch == "arm64":
            self.kernel_arch = "arm64"
        elif self.target_arch == "amd64":
            self.kernel_arch = "x86"
        else:
            click.echo("Unknown kernel architecture!!!")

    def _get_deb_architecture(self) -> None:
        if self.target_arch == "armhf":
            self.deb_arch = "armhf"
        elif self.target_arch == "arm64":
            self.deb_arch = "arm64"
        elif self.target_arch == "amd64":
            self.deb_arch = "amd64"
        else:
            click.echo("Unknown deb architecture!!!")

    def _set_kernel_targets(self) -> None:
        if not self.options.kernel_image_target:
            self.kernel_image_target = default_kernel_image_target[self.deb_arch]
        elif isinstance(self.options.kernel_image_target, str):
            self.kernel_image_target = self.options.kernel_image_target
        elif self.deb_arch in self.options.kernel_image_target:
            self.kernel_image_target = self.options.kernel_image_target[self.deb_arch]

    def _link_files_fnc_cmd(self) -> List[str]:
        return [
            " ".join(["# link files, accept wild cards"]),
            " ".join(
                ["# 1: reference dir, 2: file(s) including wild cards, 3: dst dir"]
            ),
            " ".join(["link_files() {"]),
            " ".join(['\tlocal found=""']),
            " ".join(["\tfor f in $(ls ${1}/${2})"]),
            " ".join(["\tdo"]),
            " ".join(
                [
                    "\t\tlocal rel_path=$(",
                    "realpath",
                    "--relative-to=${1}",
                    "${f}",
                    ")",
                ]
            ),
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
                        "UBUNTU_STORE_ARCH={arch}".format(arch=self.initrd_arch),
                        "snap",
                        "download",
                        "uc-initrd",
                        "--channel",
                        "{}/{}".format(
                            self.uc_series, self.options.kernel_initrd_channel
                        ),
                        "--basename",
                        "$(basename {} | cut -f1 -d'.')".format(
                            self.vanilla_initrd_snap
                        ),
                    ]
                ),
            ]

        return [
            " ".join(['echo "Geting generic initrd snap..."']),
            # only download again if files does not exist, otherwise
            # assume we are re-running build
            " ".join(
                [
                    "if [ ! -e {} ]; then".format(self.vanilla_initrd_snap),
                ]
            ),
            *cmd_download_initrd,
            " ".join(["fi"]),
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

        cmd_rebuild_modules_dep = [
            " ".join(['echo "Rebuild modules dep list first..."']),
            " ".join(
                [
                    "depmod",
                    "-b",
                    "${SNAPCRAFT_PART_INSTALL}",
                    "${KERNEL_RELEASE}",
                ]
            ),
        ]

        cmd_install_modules = [
            # install required modules to initrd
            " ".join(['echo "Installing ko modules to initrd..."']),
            " ".join(['install_modules=""']),
            " ".join(['echo "Gathering module dependencies..."']),
            " ".join(
                ["for m in {}".format(" ".join(self.options.kernel_initrd_modules))]
            ),
            " ".join(["do"]),
            " ".join(
                [
                    '\tinstall_modules="${install_modules}',
                    "$(" "modprobe",
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
                        "$(" "realpath",
                        "--relative-to=${SNAPCRAFT_PART_INSTALL}",
                        "${m}" ")",
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
                        "if [ -e ${initrd_unpacked_path_main}/lib/modules/${KERNEL_RELEASE} ]; then"
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
            ]
        )

        # gather firmware files
        cmd_copy_initrd_overlay = [
            " ".join(['echo "Installing initrd overlay..."']),
            " ".join(
                ["for f in {}".format(" ".join(self.options.kernel_initrd_firmware))]
            ),
            " ".join(["do"]),
            # firmware can be from kernel build or from stage
            # firmware from kernel build takes preference
            " ".join(
                [
                    "\tif !",
                    "link_files",
                    "${SNAPCRAFT_PART_INSTALL}/lib",
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
                            "{}".format(self.options.kernel_initrd_overlay),
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
                    ["for a in {}".format(" ".join(self.options.kernel_initrd_addons))]
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
                    "[ -e ${SNAPCRAFT_PART_INSTALL}/initrd.img ]",
                    "&&",
                    "rm -rf ${SNAPCRAFT_PART_INSTALL}/initrd.img*",
                ]
            ),
        ]

        cmd_pack_initrd.extend(
            [
                " ".join(["if [ -d ${INITRD_STAGING}/early ]; then"]),
                " ".join(["\tcd ${INITRD_STAGING}/early"]),
                " ".join(
                    [
                        "\t" "find . | cpio --create --format=newc --owner=0:0 > ",
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
                        "{} >> ".format(self._compression_cmd()),
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
            *cmd_rebuild_modules_dep,
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
                options = "{} {}".format(options, opt)
        else:
            options = _compressor_options[self.options.kernel_initrd_compression]

        cmd = "{} {}".format(compressor, options)
        click.echo("Using initrd compressions command: {!r}".format(cmd))
        return cmd

    def _parse_kernel_release_cmd(self) -> List[str]:
        return [
            " ".join(['echo "Parsing created kernel release..."']),
            " ".join(
                [
                    "KERNEL_RELEASE=$(cat ${SNAPCRAFT_PART_INSTALL}/usr/src/linux-headers-*/include/config/kernel.release)",
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
                    "mv",
                    "${SNAPCRAFT_PART_INSTALL}/boot/*",
                    "${SNAPCRAFT_PART_INSTALL}/",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_INSTALL}/${KERNEL_IMAGE_TARGET}-${KERNEL_RELEASE}",
                    "${SNAPCRAFT_PART_INSTALL}/${KERNEL_IMAGE_TARGET}",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_INSTALL}/${KERNEL_IMAGE_TARGET}",
                    "${SNAPCRAFT_PART_INSTALL}/kernel.img",
                ]
            ),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_INSTALL}/System.map-${KERNEL_RELEASE}",
                    "${SNAPCRAFT_PART_INSTALL}/System.map",
                ]
            ),
        ]
        return cmd

    def _arrange_install_dir_cmd(self) -> Set[str]:
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
                    "-rf",
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

    def _install_config_cmd(self) -> Set[str]:
        # install .config as config-$version
        return [
            " ".join([""]),
            " ".join(['echo "Installing kernel config..."']),
            " ".join(
                [
                    "ln",
                    "-f",
                    "${SNAPCRAFT_PART_INSTALL}/config-${KERNEL_RELEASE}",
                    "${SNAPCRAFT_PART_INSTALL}/.config",
                ]
            ),
        ]

    def _make_efi_cmd(self) -> Set[str]:
        kernel_f = "${KERNEL_IMAGE_TARGET}-${KERNEL_RELEASE}"
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
                    ".linux={}".format(kernel_p),
                    "--change-section-vma",
                    ".linux=0x40000",
                    "--add-section",
                    ".initrd={}".format(initrd_p),
                    "--change-section-vma",
                    ".initrd=0x3000000",
                    "/usr/lib/systemd/boot/efi/linux{}.efi.stub".format(arch),
                    efi_img_p,
                ]
            ),
        ]

    def get_build_snaps(self) -> Set[str]:
        return set()

    def get_build_packages(self) -> Set[str]:
        build_packages = {
            "bc",
            "kmod",
            "xz-utils",
            "initramfs-tools-core",
            "systemd",
            "lz4",
            "curl",
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
            "KERNEL_IMAGE_TARGET": self.kernel_image_target,
        }
        return env

    def _get_post_install_cmd(self) -> Set[str]:
        return [
            " ".join(["\n"]),
            *self._parse_kernel_release_cmd(),
            " ".join(["\n"]),
            *self._copy_vmlinuz_cmd(),
            " ".join([""]),
            *self._make_initrd_cmd(),
            " ".join([""]),
        ]

    def _get_install_command(self) -> Set[str]:
        # install to installdir
        cmd = [
            " ".join(['echo "Sorting install directory..."']),
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

    def get_build_commands(self) -> List[str]:
        click.echo("Getting build commands...")
        return [
            " ".join(['echo "PATH=$PATH"']),
            " ".join(['echo "SNAPCRAFT_PART_SRC=$SNAPCRAFT_PART_SRC"']),
            " ".join([""]),
            *self._link_files_fnc_cmd(),
            " ".join([""]),
            *self._download_generic_initrd_cmd(),
            " ".join([""]),
            *self._get_install_command(),
            " ".join(["\n"]),
            " ".join(['echo "Initrd build finished!"']),
        ]

    @property
    def out_of_source_build(self):
        # user src dir without need to link it to build dir, which takes ages
        return True
