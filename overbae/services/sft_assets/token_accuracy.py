import torch


class TokenAccuracy:
    def __init__(self, model, *, max_logit_bytes=64 * 1024 * 1024):
        self.head = model.get_output_embeddings()
        self.labels = None
        self.counts = None
        self.max_logit_bytes = max_logit_bytes
        if self.head is None:
            raise ValueError("Token accuracy requires an output embedding head.")
        # Fused CE bypasses lm_head.forward and returns empty logits. Capture only
        # the final decoder output, not every layer or a second model forward.
        self.handle = model.get_decoder().register_forward_hook(self.capture)

    def capture(self, module, args, output):
        if self.labels is None:
            return
        with torch.no_grad():
            hidden = output[0]
            if hidden.shape[:2] != self.labels.shape:
                raise ValueError("Token accuracy decoder output does not match the labels.")
            weight = self.head.weight
            chunk_size = max(
                1, self.max_logit_bytes // (weight.shape[0] * max(4, weight.element_size()))
            )
            counts = torch.zeros((len(self.labels), 2), dtype=torch.long, device=weight.device)
            for row in range(len(self.labels)):
                for start in range(0, hidden.shape[1] - 1, chunk_size):
                    end = min(start + chunk_size, hidden.shape[1] - 1)
                    targets = self.labels[row, start + 1 : end + 1].to(hidden.device)
                    mask = targets != -100
                    targets = targets[mask].to(weight.device)
                    if targets.numel() == 0:
                        continue
                    states = hidden[row, start:end][mask].to(weight.device)
                    predictions = self.head(states).argmax(dim=-1)
                    counts[row, 0] += (predictions == targets).sum()
                    counts[row, 1] += targets.numel()
            self.counts = counts
