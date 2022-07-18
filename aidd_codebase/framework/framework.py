from typing import Any, Callable, Dict, List, Optional, Union

import pytorch_lightning as pl
import torch
from aidd_codebase.models.metrics.loss import LogitLoss, LossChoice
from aidd_codebase.models.metrics.metrics import Metric
from aidd_codebase.models.modelchoice import ModelChoice
from aidd_codebase.models.optimizers.optimizers import OptimizerChoice
from aidd_codebase.models.optimizers.scheduling import Scheduler
from aidd_codebase.utils.initiator import ParameterInitialization
from aidd_codebase.utils.typescripts import Tensor

from .loggers import LoggerPL


class ModelFramework(pl.LightningModule):
    def __init__(
        self,
        model: str,
        model_args: Dict,
        loss: str,
        optimizer: str,
        metrics: Optional[List[str]] = None,
        scheduler: Optional[str] = None,
        pl_loggers: Optional[List[str]] = None,
        initialize_model: Optional[str] = None,
    ) -> None:
        super().__init__()

        # saving parameters
        self.save_hyperparameters()

        # Set Model
        self.model = ModelChoice.get_choice(model)
        self.model = self.model(model_args=model_args)
        if initialize_model:
            param_init = ParameterInitialization(method=initialize_model)
            self.model = param_init.initialize_model(self.model)

        # setting parameters
        self.loss = LossChoice.get_choice(loss)
        self.loss = LogitLoss(self.loss(reduction="mean", ignore_index=0))

        self.metrics = metrics

        self.optimizer = OptimizerChoice.get_choice(optimizer)
        self.optimizer = self.optimizer(
            self.model.parameters(), lr=0.0001, betas=(0.9, 0.98), eps=1e-9
        )

        self.scheduler = scheduler
        self.pl_loggers = pl_loggers

        self.metrics_list: Dict[str, Metric] = {}

    def set_model(self, model: pl.LightningModule) -> None:
        self.model = model

    def set_loop(self, loop: Callable) -> None:
        self.training_step_imported = loop
        self.validation_step_imported = loop
        self.test_step_imported = loop

    def training_step(self, batch: Any, batch_idx: int):
        return self.training_step_imported(
            self, batch, batch_idx, stage="train"
        )

    def validation_step(self, batch: Any, batch_idx: int):
        return self.validation_step_imported(
            self, batch, batch_idx, stage="validation"
        )

    def test_step(self, batch: Any, batch_idx: int):
        return self.test_step_imported(self, batch, batch_idx, stage="test")

    def forward(self, *args, **kwargs):
        return self.model(*args, **kwargs)

    def optimizer_step(self, *args, **kwargs):
        super().optimizer_step(*args, **kwargs)
        if self.scheduler:
            self.scheduler.step()

    def configure_optimizers(self):
        optimizer = self.optimizer

        if self.scheduler:
            self.scheduler = self.scheduler.set_optimizer(optimizer)

        return optimizer

    def _init_metrics(self, stage: str) -> None:
        loss_key = self.get_loss_key(stage)
        self.metrics_list[stage] = {
            k: Metric()
            for k in list(
                [(loss_key, *self.metrics) if self.metrics else loss_key]
            )
        }

    def _log_dict(
        self, dict: Dict, batch_size: Optional[Tensor] = None
    ) -> None:
        """Logs everything in a dict."""
        for key, value in dict.items():
            self.log(key, value, batch_size=batch_size)

    def _log_metrics(
        self,
        name: str,
        metric: Metric,
        batch_size: Optional[Tensor] = None,
    ) -> None:
        self.log(
            name,
            metric.values[-1],
            batch_size=batch_size,
        )

    def _log_epoch_metrics(self, metrics: List[Metric]) -> None:
        if self.pl_loggers:
            for logger in self.pl_loggers:
                logger.log_scalar(metrics=metrics, epoch=self.current_epoch)

    def on_train_start(self) -> None:
        self._init_metrics(stage="train")

    def on_training_epoch_start(self) -> None:
        """Called before every training epoch."""
        self._init_metrics(stage="train")

    def on_validation_epoch_start(self) -> None:
        """Called before every validation epoch."""
        self._init_metrics(stage="validation")

    def on_test_epoch_start(self) -> None:
        """Called before every test epoch."""
        self._init_metrics(stage="test")

    def on_training_batch_end(
        self,
        outputs: Any,
        batch: Any,
        batch_idx: int,
        dataloader_idx: int,
    ) -> None:
        x, _ = batch
        batch_size = x.shape[1]
        print(self.metrics_list)
        print(self.metrics_list["train"])
        for name, metric in self.metrics_list["train"].items():
            self._log_metrics(
                name,
                metric,
                torch.tensor(batch_size, device=self.device),
            )

    def on_validation_batch_end(
        self,
        outputs: Union[Tensor, Dict[str, Any], None],
        batch: Any,
        batch_idx: int,
        dataloader_idx: int,
    ) -> None:
        x, _ = batch
        batch_size = x.shape[1]
        for name, metric in self.metrics_list["validation"].items():
            self._log_metrics(
                name,
                metric,
                torch.tensor(batch_size, device=self.device),
            )

    def on_test_batch_end(
        self,
        outputs: Union[Tensor, Dict[str, Any], None],
        batch: Any,
        batch_idx: int,
        dataloader_idx: int,
    ) -> None:
        x, _ = batch
        batch_size = x.shape[1]
        for name, metric in self.metrics_list["test"].items():
            self._log_metrics(
                name,
                metric,
                torch.tensor(batch_size, device=self.device),
            )

    def training_epoch_end(self, outputs: List[Union[Tensor, Dict[str, Any]]]):
        """This function is called after every epoch"""
        self._log_epoch_metrics(self.metrics_list["train"])

    def validation_epoch_end(
        self, outputs: List[Union[Tensor, Dict[str, Any]]]
    ):
        """This function is called after every epoch"""
        self._log_epoch_metrics(self.metrics_list["validation"])

    def test_epoch_end(self, outputs: List[Union[Tensor, Dict[str, Any]]]):
        """This function is called after every epoch"""
        self._log_epoch_metrics(self.metrics_list["test"])

    @staticmethod
    def get_loss_key(stage: str) -> str:
        if stage == "train":
            loss_key = "loss"
        elif stage == "validation":
            loss_key = "val_loss"
        else:
            loss_key = "test_loss"

        return loss_key
