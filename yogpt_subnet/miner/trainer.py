import argparse
import asyncio
import json
import os
import signal
import ssl
import sys
import time

import aiohttp
import pyfiglet
from communex.module.module import Module, endpoint
from communex.client import CommuneClient
from communex._common import get_node_url
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

# from yogpt_subnet.miner.finetune.gpt_fine_tune import \
#     fine_tune_gpt  # type:ignore
from yogpt_subnet.miner.finetune.llama_fine_tune import \
    fine_tune_llama  # type:ignore
# from yogpt_subnet.miner.finetune.open_elm import \
#     fine_tune_openELM  # type:ignore
# from yogpt_subnet.miner.finetune.gemma_fine_tune import fine_tune_gemma  #type: ignore

class Trainer(Module):
    def __init__(self):
        super().__init__()
        self.console = Console()

    async def run_pipeline(self, model_type, dataset_id, epochs, batch_size, learning_rate, hf_token, job_id,miner_uid):
        """
        Dynamically select and run the appropriate pipeline based on model_type.
        """
        pipelines = {
            "llama2": fine_tune_llama,
            # "gpt2": fine_tune_gpt
        }

        if model_type not in pipelines:
            self.console.log(f"[red]Unsupported model type: {model_type}. Available options: {list(pipelines.keys())}[/red]")
            return

        self.console.log(f"[blue]Starting pipeline for model type: {model_type}[/blue]")
        try:
            pipeline_function = pipelines[model_type]
            model_repo_url, loss, accuracy, total_pipeline_time = await pipeline_function(
                dataset_id=dataset_id,
                epochs=epochs,
                batch_size=batch_size,
                learning_rate=learning_rate,
                hf_token=hf_token,
                job_id=job_id,
                miner_uid=miner_uid
            )
            if model_repo_url:
                self.console.log(f"[green]Model uploaded to: {model_repo_url}[/green]")
                self.console.log(f"[green]Total pipeline time: {total_pipeline_time}[/green]")
            else:
                self.console.log(f"[red]Pipeline for Job ID {job_id} did not return a model URL[/red]")
        except Exception as e:
            self.console.log(f"[red]Error while running pipeline for Job ID {job_id}: {str(e)}[/red]")
 



