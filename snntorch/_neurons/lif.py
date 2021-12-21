import torch
import torch.nn as nn


__all__ = [
    "LIF",
    "_SpikeTensor",
    "_SpikeTorchConv",
]

dtype = torch.float


class LIF(nn.Module):
    """Parent class for leaky integrate and fire neuron models."""

    instances = []
    """Each :mod:`snntorch.LIF` neuron (e.g., :mod:`snntorch.Synaptic`) will populate the :mod:`snntorch.LIF.instances` list with a new entry.
    The list is used to initialize and clear neuron states when the argument `init_hidden=True`."""

    def __init__(
        self,
        beta,
        threshold=1.0,
        spike_grad=None,
        init_hidden=False,
        inhibition=False,
        learn_beta=False,
        learn_threshold=False,
        reset_mechanism="subtract",
        output=False,
    ):
        super(LIF, self).__init__()
        LIF.instances.append(self)

        # self.threshold = threshold
        self.init_hidden = init_hidden
        self.inhibition = inhibition
        self.reset_mechanism = reset_mechanism
        self.output = output

        # TODO: this way, people can provide their own
        # 1) shape (one constant per layer or one per neuron)
        # 2) initial distribution
        if not isinstance(beta, torch.Tensor):
            beta = torch.as_tensor(beta)  # TODO: or .tensor() if no copy
        if learn_beta:
            self.beta = nn.Parameter(beta)
        else:
            self.register_buffer("beta", beta)

        if not isinstance(threshold, torch.Tensor):
            threshold = torch.as_tensor(threshold)
        if learn_threshold:
            self.threshold = nn.Parameter(threshold)
        else:
            self.register_buffer("threshold", threshold)

        if spike_grad is None:
            self.spike_grad = self.Heaviside.apply
        else:
            self.spike_grad = spike_grad

        if reset_mechanism != "subtract" and reset_mechanism != "zero":
            raise ValueError(
                "reset_mechanism must be set to either 'subtract' or 'zero'."
            )

    def fire(self, mem):
        """Generates spike if mem > threshold.
        Returns spk."""
        mem_shift = mem - self.threshold
        spk = self.spike_grad(mem_shift)

        return spk

    def fire_inhibition(self, batch_size, mem):
        """Generates spike if mem > threshold, only for the largest membrane. All others neurons will be inhibited for that time step.
        Returns spk."""
        mem_shift = mem - self.threshold
        index = torch.argmax(mem_shift, dim=1)
        spk_tmp = self.spike_grad(mem_shift)

        mask_spk1 = torch.zeros_like(spk_tmp)
        mask_spk1[torch.arange(batch_size), index] = 1
        spk = spk_tmp * mask_spk1
        # reset = spk.clone().detach()

        return spk

    def mem_reset(self, mem):
        """Generates detached reset signal if mem > threshold.
        Returns reset."""
        mem_shift = mem - self.threshold
        reset = self.spike_grad(mem_shift).clone().detach()

        return reset

    @classmethod
    def init(cls):
        """Removes all items from :mod:`snntorch.LIF.instances` when called."""
        cls.instances = []

    @staticmethod
    def init_leaky():
        """
        Used to initialize mem as an empty SpikeTensor.
        ``init_flag`` is used as an attribute in the forward pass to convert the hidden states to the same as the input.
        """
        # print(f"init_leaky executing")
        mem = _SpikeTensor(init_flag=False)

        return mem

    @staticmethod
    def init_synaptic():
        """Used to initialize syn and mem as an empty SpikeTensor.
        ``init_flag`` is used as an attribute in the forward pass to convert the hidden states to the same as the input.
        """

        syn = _SpikeTensor(init_flag=False)
        mem = _SpikeTensor(init_flag=False)

        return syn, mem

    @staticmethod
    def init_stein():
        """Used to initialize syn and mem as an empty SpikeTensor.
        ``init_flag`` is used as an attribute in the forward pass to convert the hidden states to the same as the input.
        """
        return LIF.init_synaptic()

    @staticmethod
    def init_lapicque():
        """
        Used to initialize mem as an empty SpikeTensor.
        ``init_flag`` is used as an attribute in the forward pass to convert the hidden states to the same as the input.
        """

        return LIF.init_leaky()

    @staticmethod
    def init_alpha():
        """Used to initialize syn_exc, syn_inh and mem as an empty SpikeTensor.
        ``init_flag`` is used as an attribute in the forward pass to convert the hidden states to the same as the input.
        """
        syn_exc = _SpikeTensor(init_flag=False)
        syn_inh = _SpikeTensor(init_flag=False)
        mem = _SpikeTensor(init_flag=False)

        return syn_exc, syn_inh, mem

    @staticmethod
    def detach(*args):
        """Used to detach input arguments from the current graph.
        Intended for use in truncated backpropagation through time where hidden state variables are global variables."""
        for state in args:
            state.detach_()

    @staticmethod
    def zeros(*args):
        """Used to clear hidden state variables to zero.
        Intended for use where hidden state variables are global variables."""
        for state in args:
            state = torch.zeros_like(state)

    @staticmethod
    class Heaviside(torch.autograd.Function):
        """Default spiking function for neuron.

        **Forward pass:** Heaviside step function shifted.

        .. math::

            S=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        **Backward pass:** Heaviside step function shifted.

        .. math::

            \\frac{∂S}{∂U}=\\begin{cases} 1 & \\text{if U ≥ U$_{\\rm thr}$} \\\\
            0 & \\text{if U < U$_{\\rm thr}$}
            \\end{cases}

        Although the backward pass is clearly not the analytical solution of the forward pass, this assumption holds true on the basis that a reset necessarily occurs after a spike is generated when :math:`U ≥ U_{\\rm thr}`."""

        @staticmethod
        def forward(ctx, input_):
            out = (input_ > 0).float()
            ctx.save_for_backward(out)
            return out

        @staticmethod
        def backward(ctx, grad_output):
            (out,) = ctx.saved_tensors
            grad = grad_output * out
            return grad


class _SpikeTensor(torch.Tensor):
    """Inherits from torch.Tensor with additional attributes.
    ``init_flag`` is set at the time of initialization.
    When called in the forward function of any neuron, they are parsed and replaced with a torch.Tensor variable.
    """

    @staticmethod
    def __new__(cls, *args, init_flag=False, **kwargs):
        return super().__new__(cls, *args, **kwargs)

    def __init__(
        self,
        *args,
        init_flag=True,
    ):
        # super().__init__() # optional
        self.init_flag = init_flag


def _SpikeTorchConv(*args, input_):
    """Convert SpikeTensor to torch.Tensor of the same size as ``input_``."""

    states = []
    # if len(input_.size()) == 0:
    #     _batch_size = 1  # assume batch_size=1 if 1D input
    # else:
    #     _batch_size = input_.size(0)
    if (
        len(args) == 1 and type(args) is not tuple
    ):  # if only one hidden state, make it iterable
        args = (args,)
    for arg in args:
        arg = torch.Tensor(arg)  # wash away the SpikeTensor class
        arg = torch.zeros_like(input_, requires_grad=True)
        states.append(arg)
    if len(states) == 1:  # otherwise, list isn't unpacked
        return states[0]

    return states
