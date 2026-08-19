Python extension API
====================

ScaffoldScope's supported Python API is intentionally narrow. The command-line and persisted
evidence contracts are the primary product interfaces. The objects below support third-party
context-policy and model-provider packages.

Plugin registration
-------------------

.. autodata:: scaffoldscope.plugins.PLUGIN_API_VERSION

.. autodata:: scaffoldscope.plugins.CONTEXT_POLICY_ENTRY_POINT

.. autodata:: scaffoldscope.plugins.MODEL_PROVIDER_ENTRY_POINT

.. autofunction:: scaffoldscope.plugins.context_policy_plugin

.. autofunction:: scaffoldscope.plugins.model_provider_plugin

.. autoclass:: scaffoldscope.plugins.PluginRegistration
   :members:

Factory requests and protocols
------------------------------

.. autoclass:: scaffoldscope.plugins.ContextPolicyRequest
   :members:

.. autoclass:: scaffoldscope.plugins.ModelProviderRequest
   :members:

.. autoclass:: scaffoldscope.plugins.ContextPolicyFactory
   :members:

.. autoclass:: scaffoldscope.plugins.ModelProviderFactory
   :members:

Discovery and provenance
------------------------

.. autoclass:: scaffoldscope.plugins.PluginKind
   :members:

.. autoclass:: scaffoldscope.plugins.PluginInfo
   :members:

.. autoclass:: scaffoldscope.plugins.LoadedPlugin
   :members:

.. autoclass:: scaffoldscope.plugins.PluginRegistry
   :members:

.. autofunction:: scaffoldscope.plugins.normalize_plugin_name

Errors
------

.. autoexception:: scaffoldscope.plugins.PluginError

.. autoexception:: scaffoldscope.plugins.PluginDiscoveryError

.. autoexception:: scaffoldscope.plugins.PluginCollisionError

.. autoexception:: scaffoldscope.plugins.PluginLoadError

.. autoexception:: scaffoldscope.plugins.PluginCompatibilityError

Context-policy contracts
------------------------

Policy plugins return a :class:`~scaffoldscope.context.ContextPolicy` and use the canonical
trajectory types below. Read :doc:`../extensions` before implementing one; the evidence and
atomic-bundle requirements are part of the contract.

.. autoclass:: scaffoldscope.schema.VariantConfig
   :members:

.. autoclass:: scaffoldscope.tokenization.Char4TokenCounter
   :members:

.. autoclass:: scaffoldscope.context.Message
   :members:

.. autoclass:: scaffoldscope.context.MessageBundle
   :members:

.. autoclass:: scaffoldscope.context.Trajectory
   :members:

.. autoclass:: scaffoldscope.context.ContextBudget
   :members:

.. autoclass:: scaffoldscope.context.ContextDecision
   :members:

.. autoclass:: scaffoldscope.context.ContextView
   :members:

.. autoclass:: scaffoldscope.context.ContextPolicy
   :members:

Model-provider contracts
------------------------

Provider plugins return an object satisfying :class:`~scaffoldscope.models.ChatModel`.

.. autoclass:: scaffoldscope.schema.ModelConfig
   :members:

.. autoclass:: scaffoldscope.schema.ConstraintSpec
   :members:

.. autoclass:: scaffoldscope.schema.TaskSpec
   :members:

.. autoclass:: scaffoldscope.models.Usage
   :members:

.. autoclass:: scaffoldscope.models.ModelResponse
   :members:

.. autoclass:: scaffoldscope.models.ChatModel
   :members:

Compatibility policy
--------------------

The symbols documented on this page are the supported extension surface for plugin API version
1. Other modules are readable implementation code, not a blanket stability promise. Persisted
JSON evidence follows the versioning rules in :doc:`../results-schema`.
