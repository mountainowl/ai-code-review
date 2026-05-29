"""Source-control provider abstraction.

The poller is provider-agnostic: it sequences the review pipeline against
an :class:`~llm_reviewer.scm.base.ScmProvider` without knowing whether the
backend is GitLab or GitHub. :func:`get_provider` resolves the concrete
implementation from ``cfg.provider``.

Adding a provider is: implement the :class:`ScmProvider` protocol in a new
module and register it in :func:`get_provider`. Nothing in the poller
changes.
"""

from __future__ import annotations

from llm_reviewer.review_config import ReviewConfig
from llm_reviewer.scm.base import ScmProvider


def get_provider(cfg: ReviewConfig) -> ScmProvider:
    """Return the provider implementation for ``cfg.provider``.

    Raises :class:`ValueError` for an unknown provider — though
    :func:`llm_reviewer.review_config.review_config_from_dict` already
    validates the value, so this is a defense-in-depth guard.
    """
    if cfg.provider == "gitlab":
        from llm_reviewer.scm.gitlab import GitLabProvider

        return GitLabProvider()
    if cfg.provider == "github":
        from llm_reviewer.scm.github import GitHubProvider

        return GitHubProvider()
    raise ValueError(f"unknown provider: {cfg.provider!r}")


__all__ = ["ScmProvider", "get_provider"]
