# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import dataclasses
import platform
from typing import Dict, Generic, Optional, TypeVar

from .exceptions import InvalidConfiguration

T = TypeVar("T")
U = TypeVar("U")

PLATFORM_MAPPING = {
    "Darwin": "macos",
    "Windows": "windows",
    "Linux": "linux",
    "default": "default",
}


@dataclasses.dataclass(frozen=True)
class PlatformAware(Generic[T]):
    default: T
    windows: Optional[T] = None
    macos: Optional[T] = None
    linux: Optional[T] = None

    @staticmethod
    def from_json(value: object, field_name: str) -> "Optional[PlatformAware[T]]":
        if value is None:
            return None
        elif isinstance(value, dict):
            if len(value) == 0:
                return None

            invalid_keys = value.keys() - PLATFORM_MAPPING.values()
            if not len(invalid_keys) == 0:
                raise InvalidConfiguration(
                    f"Configuration `{field_name}` only supports platforms: "
                    f"{PLATFORM_MAPPING.values()} but got: `{invalid_keys}`."
                )

            default: T = (
                value["default"]
                if "default" in value
                else value[sorted(value.keys())[0]]
            )
            return PlatformAware(
                default=default,
                windows=value["windows"] if "windows" in value else None,
                macos=value["macos"] if "macos" in value else None,
                linux=value["linux"] if "linux" in value else None,
            )
        else:
            # pyre-ignore[7]: we can't verify the type of value
            return PlatformAware(default=value)

    @staticmethod
    def merge(
        base: "Optional[PlatformAware[U]]", override: "Optional[PlatformAware[U]]"
    ) -> "Optional[PlatformAware[U]]":
        if base is None:
            return override
        elif override is None:
            return base
        else:
            windows = override.windows
            macos = override.macos
            linux = override.linux
            # pyre-ignore[7]: Pyre wasn't able to infer the right return type
            return PlatformAware(
                default=override.default,
                windows=windows if windows is not None else base.windows,
                macos=macos if macos is not None else base.macos,
                linux=linux if linux is not None else base.linux,
            )

    def get(self, key: Optional[str] = None) -> T:
        if key is None:
            key = PLATFORM_MAPPING[platform.system()]
        value: T = self.__getattribute__(key)
        return value if value is not None else self.default

    def to_json(self) -> Dict[str, T]:
        result = {"default": self.default}
        if self.windows is not None:
            result["windows"] = self.windows
        if self.linux is not None:
            result["linux"] = self.linux
        if self.macos is not None:
            result["macos"] = self.macos
        return result
