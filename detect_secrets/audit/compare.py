"""
The compare module allows developers/analysts to determine the difference between two
different baseline files. This allows easier configuration of plugin settings.

For effective use, a few assumptions are made:
    1. Baselines are sorted by (filename, line_number, hash).
        This allows for a deterministic order, when doing a side-by-side
        comparison.

    2. Baselines are generated for the same codebase snapshot.
        This means that we won't have cases where secrets are moved around;
        only added or removed.

NOTE: We don't want to do a version check, because we want to be able to
use this functionality across versions (to see how the new version fares
compared to the old one).
"""
from typing import Any
from typing import Dict
from typing import Iterator
from typing import Optional
from typing import Tuple
from typing import Type
from typing import Union

from . import io
from ..core import baseline
from ..core.potential_secret import PotentialSecret
from ..core.secrets_collection import SecretsCollection
from ..custom_types import SecretContext
from ..exceptions import NoLineNumberError
from ..exceptions import SecretNotFoundOnSpecifiedLineError
from ..settings import transient_settings
from ..util.code_snippet import get_code_snippet
from ..util.color import AnsiColor
from ..util.color import colorize
from .common import get_raw_secret_from_file
from .common import open_file
from .iterator import BidirectionalIterator


def compare_baselines(old_baseline_filename: str, new_baseline_filename: str) -> None:
    if old_baseline_filename == new_baseline_filename:
        io.print_error('This is the same file!')
        return

    old_baseline, old_config = _get_baseline_from_file(old_baseline_filename)
    new_baseline, new_config = _get_baseline_from_file(new_baseline_filename)

    # We want to display all entries, so make sure all files are present.
    old_baseline.trim()
    new_baseline.trim()

    try:
        _display_difference_to_user((old_baseline, old_config), (new_baseline, new_config))
    except NoLineNumberError as e:
        io.print_error(str(e))


def _get_baseline_from_file(filename: str) -> Tuple[SecretsCollection, Dict[str, Any]]:
    data = baseline.upgrade(baseline.load_from_file(filename))
    config = {
        'plugins_used': data['plugins_used'],
        'filters_used': [] if 'filters_used' not in data else data['filters_used'],
    }

    return baseline.load(data, filename), config


def _compare_baselines(
    old_baseline: SecretsCollection,
    new_baseline: SecretsCollection,
) -> Iterator[Tuple[str, Optional[PotentialSecret], Optional[PotentialSecret]]]:
    """
    :returns: (filename, left_secret, right_secret)
        `filename` is needed to know which file to display;
        if `left_secret` is None, then it's a newly added secret;
        if `right_secret` is None, then it's a deleted secret
    """
    class LeftSecret(Exception):
        pass

    class RightSecret(Exception):
        pass

    left_secrets = [secret for _, secret in old_baseline]
    right_secrets = [secret for _, secret in new_baseline]

    left_index = 0
    right_index = 0
    while left_index < len(left_secrets) or right_index < len(right_secrets):
        try:
            # This allows us to delay execution of the exception handling, until we had a chance
            # to initialize both variables. Either one must at least pass, otherwise the while
            # statement will be False.
            exception: Optional[Union[Type[LeftSecret], Type[RightSecret]]] = None
            try:
                left_secret = left_secrets[left_index]
                if not left_secret.line_number:
                    raise NoLineNumberError
            except IndexError:
                exception = RightSecret

            try:
                right_secret = right_secrets[right_index]
                if not right_secret.line_number:
                    raise NoLineNumberError
            except IndexError:
                exception = LeftSecret

            if exception:
                raise exception

            # At this point, both secrets exist. So now, we'll check for filenames.
            # If they aren't talking about the same file, then the rest of the secrets in the
            # file must not exist.
            if left_secret.filename < right_secret.filename:
                raise LeftSecret
            elif left_secret.filename > right_secret.filename:
                raise RightSecret

            # When it's the same file, we want to show it by line order of the combined
            # list.
            if left_secret.line_number < right_secret.line_number:
                raise LeftSecret
            elif left_secret.line_number > right_secret.line_number:
                raise RightSecret

            # At this point, it's the same line number, and the same filename.
            # They could be different secrets (and therefore, different places on the same line)
            # but we'll just show the alphabetically lowest one first (easier implementation).
            if left_secret.secret_hash < right_secret.secret_hash:
                raise LeftSecret
            elif left_secret.secret_hash > right_secret.secret_hash:
                raise RightSecret

            # At this point, they must be referencing the same secret value (not necessarily
            # same *secret*, since it could be detected by different plugins). However, since this
            # is a comparison, we're going to currently ignore if the secret values are the same.
            old_hash = left_secret.secret_hash
            try:
                while old_hash == left_secrets[left_index].secret_hash:
                    left_index += 1
            except IndexError:
                pass

            old_hash = right_secret.secret_hash
            try:
                while old_hash == right_secrets[right_index].secret_hash:
                    right_index += 1
            except IndexError:
                pass

        except LeftSecret:
            yield (left_secret.filename, left_secret, None)
            left_index += 1

        except RightSecret:
            yield (right_secret.filename, None, right_secret)
            right_index += 1


def _display_difference_to_user(
    old_data: Tuple[SecretsCollection, Dict[str, Any]],
    new_data: Tuple[SecretsCollection, Dict[str, Any]],
) -> None:
    """
    :param old_data: it needs both, since it can technically be scanning two different
        plugins' settings.
    :param new_data: same as `old_data`
    """
    old_baseline, old_config = old_data
    new_baseline, new_config = new_data

    iterator = BidirectionalIterator(list(_compare_baselines(old_baseline, new_baseline)))
    for filename, left_secret, right_secret in iterator:
        io.clear_screen()

        secret = left_secret if left_secret else right_secret
        config = old_config if left_secret else new_config

        try:
            with transient_settings(config):
                secret.secret_value = get_raw_secret_from_file(secret)

            context = SecretContext(
                current_index=iterator.index + 1,
                num_total_secrets=len(iterator.collection),
                secret=secret,
                header='{status}      {value}'.format(
                    status=colorize('Status:', AnsiColor.BOLD),
                    value='>> {} <<'.format(
                        colorize('REMOVED', AnsiColor.RED)
                        if not right_secret
                        else colorize('ADDED', AnsiColor.LIGHT_GREEN),
                    ),
                ),
                snippet=get_code_snippet(
                    lines=open_file(secret.filename).raw_lines,
                    line_number=secret.line_number,
                ),
            )
            io.print_context(context)

            decision = io.get_user_decision(
                can_step_back=iterator.can_step_back(),
                prompt_secret_decision=False,
            )
        except SecretNotFoundOnSpecifiedLineError as e:
            io.print_secret_not_found(
                SecretContext(
                    current_index=iterator.index + 1,
                    num_total_secrets=len(iterator.collection),
                    secret=secret,
                    error=e,
                ),
            )

            decision = io.get_user_decision(
                prompt_secret_decision=False,
                can_step_back=iterator.can_step_back(),
            )

        if decision == io.InputOptions.QUIT:
            io.print_message('Quitting...')
            break
        elif decision == io.InputOptions.BACK:
            iterator.step_back_on_next_iteration()
