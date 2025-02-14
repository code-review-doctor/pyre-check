# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import dataclasses
import itertools
import json
import logging
import re
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional, Sequence, Union

import libcst as cst

from .. import (
    command_arguments,
    configuration as configuration_module,
    log,
    statistics_collectors as collectors,
    statistics_logger,
)
from . import commands, remote_logging


LOG: logging.Logger = logging.getLogger(__name__)


def find_roots(
    configuration: configuration_module.Configuration,
    statistics_arguments: command_arguments.StatisticsArguments,
) -> Iterable[Path]:
    filter_paths = statistics_arguments.filter_paths
    if len(filter_paths) > 0:

        def to_absolute_path(given: str) -> Path:
            path = Path(given)
            return path if path.is_absolute() else Path.cwd() / path

        return {to_absolute_path(path) for path in filter_paths}

    local_root = configuration.local_root
    if local_root is not None:
        return [Path(local_root)]

    return [Path.cwd()]


def _is_excluded(path: Path, excludes: Sequence[str]) -> bool:
    try:
        return any(
            [re.match(exclude_pattern, str(path)) for exclude_pattern in excludes]
        )
    except re.error:
        LOG.warning("Could not parse `excludes`: %s", excludes)
        return False


def _should_ignore(path: Path, excludes: Sequence[str]) -> bool:
    return (
        path.name.startswith("__")
        or path.name.startswith(".")
        or _is_excluded(path, excludes)
    )


def has_py_extension_and_not_ignored(path: Path, excludes: Sequence[str]) -> bool:
    return path.suffix == ".py" and not _should_ignore(path, excludes)


def find_paths_to_parse(
    configuration: configuration_module.Configuration, paths: Iterable[Path]
) -> Iterable[Path]:
    def _get_paths_for_file(target_file: Path) -> Iterable[Path]:
        return (
            [target_file]
            if has_py_extension_and_not_ignored(target_file, configuration.excludes)
            else []
        )

    def _get_paths_in_directory(target_directory: Path) -> Iterable[Path]:
        return (
            path
            for path in target_directory.glob("**/*.py")
            if not _should_ignore(path, configuration.excludes)
        )

    return itertools.chain.from_iterable(
        _get_paths_for_file(path)
        if not path.is_dir()
        else _get_paths_in_directory(path)
        for path in paths
    )


def parse_text_to_module(text: str) -> Optional[cst.Module]:
    try:
        return cst.parse_module(text)
    except cst.ParserSyntaxError:
        return None


def parse_path_to_module(path: Path) -> Optional[cst.Module]:
    try:
        return parse_text_to_module(path.read_text())
    except FileNotFoundError:
        return None


def _collect_statistics_for_module(
    module: Union[cst.Module, cst.MetadataWrapper],
    collector_factory: Callable[[], collectors.StatisticsCollector],
) -> Dict[str, int]:
    collector = collector_factory()
    module.visit(collector)
    return collector.build_json()


def _collect_annotation_statistics(module: cst.Module) -> Dict[str, int]:
    return _collect_statistics_for_module(
        cst.MetadataWrapper(module),
        collectors.AnnotationCountCollector,
    )


def _collect_fixme_statistics(
    module: cst.Module,
) -> Dict[str, int]:
    return _collect_statistics_for_module(module, collectors.FixmeCountCollector)


def _collect_ignore_statistics(
    module: cst.Module,
) -> Dict[str, int]:
    return _collect_statistics_for_module(module, collectors.IgnoreCountCollector)


def _collect_strict_file_statistics(
    module: cst.Module,
    strict_default: bool,
) -> Dict[str, int]:
    def collector_factory() -> collectors.StrictCountCollector:
        return collectors.StrictCountCollector(strict_default)

    return _collect_statistics_for_module(module, collector_factory)


@dataclasses.dataclass(frozen=True)
class StatisticsData:
    annotations: Dict[str, int] = dataclasses.field(default_factory=dict)
    fixmes: Dict[str, int] = dataclasses.field(default_factory=dict)
    ignores: Dict[str, int] = dataclasses.field(default_factory=dict)
    strict: Dict[str, int] = dataclasses.field(default_factory=dict)


def collect_statistics(
    sources: Iterable[Path], strict_default: bool
) -> Dict[str, StatisticsData]:
    data: Dict[str, StatisticsData] = {}
    for path in sources:
        module = parse_path_to_module(path)
        if module is None:
            continue
        try:
            annotation_statistics = _collect_annotation_statistics(module)
            fixme_statistics = _collect_fixme_statistics(module)
            ignore_statistics = _collect_ignore_statistics(module)
            strict_file_statistics = _collect_strict_file_statistics(
                module, strict_default
            )
            statistics_data = StatisticsData(
                annotation_statistics,
                fixme_statistics,
                ignore_statistics,
                strict_file_statistics,
            )
            data[str(path)] = statistics_data
        except RecursionError:
            LOG.warning(f"LibCST encountered recursion error in `{path}`")
    return data


@dataclasses.dataclass(frozen=True)
class AggregatedStatisticsData:
    annotations: Dict[str, int] = dataclasses.field(default_factory=dict)
    fixmes: int = 0
    ignores: int = 0
    strict: int = 0
    unsafe: int = 0


def aggregate_statistics(data: Dict[str, StatisticsData]) -> AggregatedStatisticsData:
    aggregate_annotations = {
        "return_count": 0,
        "annotated_return_count": 0,
        "globals_count": 0,
        "annotated_globals_count": 0,
        "parameter_count": 0,
        "annotated_parameter_count": 0,
        "attribute_count": 0,
        "annotated_attribute_count": 0,
        "function_count": 0,
        "partially_annotated_function_count": 0,
        "fully_annotated_function_count": 0,
        "line_count": 0,
    }

    for statistics_data in data.values():
        for key in aggregate_annotations.keys():
            aggregate_annotations[key] += statistics_data.annotations[key]

    return AggregatedStatisticsData(
        annotations=aggregate_annotations,
        fixmes=sum(
            len(fixmes)
            for fixmes in [statistics_data.fixmes for statistics_data in data.values()]
        ),
        ignores=sum(
            len(ignores)
            for ignores in [
                statistics_data.ignores for statistics_data in data.values()
            ]
        ),
        strict=sum(
            strictness["strict_count"]
            for strictness in [
                statistics_data.strict for statistics_data in data.values()
            ]
        ),
        unsafe=sum(
            strictness["unsafe_count"]
            for strictness in [
                statistics_data.strict for statistics_data in data.values()
            ]
        ),
    )


def get_overall_annotation_percentage(data: AggregatedStatisticsData) -> float:
    total_annotation_slots = (
        data.annotations["return_count"]
        + data.annotations["parameter_count"]
        + data.annotations["attribute_count"]
        + data.annotations["globals_count"]
    )
    filled_annotation_slots = (
        data.annotations["annotated_return_count"]
        + data.annotations["annotated_parameter_count"]
        + data.annotations["annotated_attribute_count"]
        + data.annotations["annotated_globals_count"]
    )
    return filled_annotation_slots * 100.0 / total_annotation_slots


def get_summary(aggregated_data: AggregatedStatisticsData) -> str:
    annotation_rate = round(get_overall_annotation_percentage(aggregated_data), 2)
    total_suppressions = aggregated_data.fixmes + aggregated_data.ignores
    total_modules = aggregated_data.strict + aggregated_data.unsafe
    if total_modules > 0:
        strict_module_rate = round(aggregated_data.strict * 100.0 / total_modules, 2)
        unsafe_module_rate = round(aggregated_data.unsafe * 100.0 / total_modules, 2)
    else:
        strict_module_rate = 100.00
        unsafe_module_rate = 0.00
    total_function_count = aggregated_data.annotations["function_count"]
    if total_function_count > 0:
        partially_annotated_function_rate = round(
            aggregated_data.annotations["partially_annotated_function_count"]
            * 100.0
            / total_function_count,
            2,
        )
        fully_annotated_function_rate = round(
            aggregated_data.annotations["fully_annotated_function_count"]
            * 100.0
            / total_function_count,
            2,
        )
    else:
        partially_annotated_function_rate = 0.00
        fully_annotated_function_rate = 100.00
    return (
        f"Coverage summary:\nOverall annotation rate is {annotation_rate}%\n"
        + f"There are {total_suppressions} total error suppressions inline ({aggregated_data.fixmes} fixmes and {aggregated_data.ignores} ignores)\n"
        + f"Of {total_modules} modules, {strict_module_rate}% are strict and {unsafe_module_rate}% are unsafe\n"
        + f"Of {total_function_count} functions, {fully_annotated_function_rate}% are fully annotated and {partially_annotated_function_rate}% are partially annotated\n"
    )


def log_to_remote(
    configuration: configuration_module.Configuration,
    run_id: str,
    data: Dict[str, Dict[str, Dict[str, int]]],
) -> None:
    def path_to_data(category: str) -> Dict[str, Dict[str, int]]:
        return {
            path: statistics_data[category] for (path, statistics_data) in data.items()
        }

    def _log_fixmes(fixme_type: str, data: Dict[str, int], path: str) -> None:
        for error_code, count in data.items():
            statistics_logger.log_with_configuration(
                statistics_logger.LoggerCategory.FIXME_COUNTS,
                configuration,
                integers={"count": count},
                normals={
                    "run_id": run_id,
                    "code": error_code,
                    "type": fixme_type,
                    "path": path,
                },
            )

    for path, counts in path_to_data("annotations").items():
        statistics_logger.log_with_configuration(
            statistics_logger.LoggerCategory.ANNOTATION_COUNTS,
            configuration,
            integers=counts,
            normals={"run_id": run_id, "path": path},
        )
    for path, counts in path_to_data("fixmes").items():
        _log_fixmes("fixme", counts, path)
    for path, counts in path_to_data("ignores").items():
        _log_fixmes("ignore", counts, path)
    for path, counts in path_to_data("strict").items():
        statistics_logger.log_with_configuration(
            statistics_logger.LoggerCategory.STRICT_ADOPTION,
            configuration,
            integers=counts,
            normals={"run_id": run_id, "path": path},
        )


def run_statistics(
    configuration: configuration_module.Configuration,
    statistics_arguments: command_arguments.StatisticsArguments,
) -> commands.ExitCode:
    data = collect_statistics(
        find_paths_to_parse(
            configuration, find_roots(configuration, statistics_arguments)
        ),
        strict_default=configuration.strict,
    )
    if statistics_arguments.print_summary:
        aggregated_data = aggregate_statistics(data)
        LOG.warning(get_summary(aggregated_data))
        return commands.ExitCode.SUCCESS

    if statistics_arguments.aggregate:
        aggregated_data = aggregate_statistics(data)
        log.stdout.write(json.dumps(dataclasses.asdict(aggregated_data), indent=4))
    else:
        path_to_dictionary_statistics = {
            path: dataclasses.asdict(statistics_data)
            for (path, statistics_data) in data.items()
        }
        log.stdout.write(json.dumps(path_to_dictionary_statistics))
        if statistics_arguments.log_results:
            logger = configuration.logger
            if logger is None:
                LOG.warning("Skip remote logging since no logger is specified.")
            else:
                log_identifier = statistics_arguments.log_identifier
                run_id = (
                    log_identifier
                    if log_identifier is not None
                    else str(time.time_ns())
                )
                log_to_remote(configuration, run_id, path_to_dictionary_statistics)

    return commands.ExitCode.SUCCESS


@remote_logging.log_usage(command_name="statistics")
def run(
    configuration: configuration_module.Configuration,
    statistics_arguments: command_arguments.StatisticsArguments,
) -> commands.ExitCode:
    try:
        return run_statistics(configuration, statistics_arguments)
    except Exception as error:
        raise commands.ClientException(
            f"Exception occurred during statistics collection: {error}"
        ) from error
