import firebase_admin
from firebase_admin import credentials, firestore
from communex.module.module import Module
from communex.client import CommuneClient
from substrateinterface import Keypair
from loguru import logger
import os
import warnings
from dotenv import load_dotenv

warnings.filterwarnings(
    "ignore",
    message="Detected filter using positional arguments. Prefer using the 'filter' keyword argument instead."
)

load_dotenv()

cred_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

if not os.path.exists(cred_path):
    raise FileNotFoundError(f"Credential file not found: {cred_path}")

cred = credentials.Certificate(cred_path)
firebase_admin.initialize_app(cred)

db = firestore.client()

class ModelRewardChecker(Module):
    def __init__(self, key: Keypair, netuid: int, client: CommuneClient) -> None:
        super().__init__()
        self.key = key
        self.netuid = netuid
        self.client = client
        logger.info(f"Model reward checker initialized")
        self.model_thresholds = {
            "llama2-7b": {"threshold": 0.20, "training_per_hour": 1.2, "fine_tuning_time": (10, 12)},
            "OpenELM-270M": {"threshold": 0.50, "training_per_hour": 1.0, "fine_tuning_time": (3, 5)},
            "OpenELM-450M": {"threshold": 0.35, "training_per_hour": 1.3, "fine_tuning_time": (6, 8)},
            "OpenELM-3B": {"threshold": 0.20, "training_per_hour": 2.2, "fine_tuning_time": (10, 12)},
            "GPT2": {"threshold": 0.50, "training_per_hour": 1.5, "fine_tuning_time": (3, 5)},
            "LLama3B": {"threshold": 0.35, "training_per_hour": 2.2, "fine_tuning_time": (6, 8)}
        }
        self.default_model_threshold = {
            "threshold": 4.0,
            "training_per_hour": 1.0,
            "fine_tuning_time": (0, 2)
        }

    def calculate_reward(self, job_data):
        logger.info(f"Processing job: {job_data.get('jobId')}")

        model_tuned = job_data.get('model_tuned')
        if not model_tuned:
            logger.info(f"No model_tuned provided for job '{job_data.get('jobId')}', using default thresholds.")
            model_info = self.default_model_threshold
        else:
            model_info = self.model_thresholds.get(model_tuned, self.default_model_threshold)

        try:
            loss = float(job_data.get('loss', None))
        except (TypeError, ValueError):
            logger.error(f"Invalid loss value for job '{job_data.get('jobId')}': {job_data.get('loss')}")
            return 0, f"Invalid loss value: {job_data.get('loss')}"

        duration_str = job_data.get('totalPipelineTime')
        model_created = job_data.get('huggingFaceRepoId')

        if not model_created or 'huggingface' not in model_created.lower():
            return 0, "No valid Hugging Face model created"

        threshold = model_info['threshold']
        training_per_hour = model_info['training_per_hour']
        min_time, max_time = model_info['fine_tuning_time']

        if duration_str is None:
            logger.error(f"Job '{job_data.get('jobId')}' has invalid duration format: None")
            return 0, "Invalid duration format: None"

        try:
            duration_parts = str(duration_str).split(':')
            if len(duration_parts) == 3:  # HH:MM:SS format
                hours, minutes, seconds = map(int, duration_parts)
                duration = hours + minutes / 60 + seconds / 3600
            elif len(duration_parts) == 2:  # HH:MM format
                hours, minutes = map(int, duration_parts)
                duration = hours + minutes / 60
            else:
                logger.error(f"Invalid duration format: {duration_str}")
                return 0, f"Invalid duration format: {duration_str}"
        except ValueError:
            logger.error(f"Unable to parse duration: {duration_str}")
            return 0, f"Unable to parse duration: {duration_str}"

        if loss is None or loss >= threshold:
            logger.info(f"Loss {loss} exceeds or equals threshold {threshold} for job '{job_data.get('jobId')}'")
            return 0, "Loss exceeds or equals threshold"

        if duration < min_time:
            return 0, f"Training completed too quickly. Expected minimum {min_time} hours, but took {duration:.2f} hours"

        if duration > max_time:
            return 0, f"Training took longer than expected. Maximum allowed time is {max_time} hours, but took {duration:.2f} hours"

        reward = training_per_hour * duration
        logger.info(f"Calculated reward: {reward} for job '{job_data.get('jobId')}'")
        return reward, f"Reward granted for {duration:.2f} hours of training"

    def reward_completed_jobs(self):
        logger.info("Checking completed jobs for rewards...")
        jobs_ref = db.collection('completed_jobs')
        completed_jobs = jobs_ref.stream()

        score_dict = {}
        for job in completed_jobs:
            job_data = job.to_dict()
            reward, message = self.calculate_reward(job_data)
            logger.info(f"Job '{job_data.get('jobId')}': {reward} - {message}")
            
            if reward > 0:
                score = reward / 100
                score_dict[job_data['minerId']] = score
                job.reference.update({
                    'status': 'rewarded',
                    'reward': reward,
                    'reward_message': message
                })
            else:
                job.reference.update({
                    'status': 'not_rewarded',
                    'reward_message': message
                })
        
        if score_dict:
            self.set_weights(score_dict)
        
    def set_weights(self, score_dict: dict[str, float]) -> None:
        """
        Sets the weights for miners after mapping their SS58 addresses to UIDs.
        """
        logger.info(f"Setting weights for miners: {score_dict}")

        # Step 1: Cut the scores to the maximum allowed weights
        score_dict = self.cut_to_max_allowed_weights(score_dict)

        # Step 2: Get the map of UIDs and SS58 addresses from the network
        modules_keys = self.client.query_map_key(self.netuid)
        
        # Step 3: Map SS58 addresses to UIDs
        uid_scores = {}
        for ss58_address, score in score_dict.items():
            miner_uid = next((uid for uid, address in modules_keys.items() if address == ss58_address), None)
            
            if miner_uid is not None:
                uid_scores[miner_uid] = score
            else:
                logger.warning(f"SS58 address {ss58_address} not found in network, skipping.")
        
        # If no valid UIDs were found, log an error and return
        if not uid_scores:
            logger.error("No valid UIDs were found for the provided SS58 addresses.")
            return

        # Step 4: Assign weights to UIDs
        weighted_scores = {uid: self.assign_weight(score) for uid, score in uid_scores.items()}
        uids = list(weighted_scores.keys())
        weights = list(weighted_scores.values())

        logger.info(f"UIDs listed: {uids}")
        logger.info(f"Weights gained: {weights}")

        # Step 5: Try voting with the assigned weights
        try:
            self.client.vote(key=self.key, uids=uids, weights=weights, netuid=self.netuid)
        except Exception as e:
            logger.error(f"Error setting weights: {e}")

    def assign_weight(self, score):
        max_score = 1.0 
        weight = int(score * 1000 / max_score)
        return weight
    
    def cut_to_max_allowed_weights(self, score_dict: dict[int, float], max_allowed_weights: int = 420) -> dict[int, float]:
        sorted_scores = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)
        cut_scores = sorted_scores[:max_allowed_weights]
        logger.info(f"Scores after cutting to max allowed weights: {cut_scores}")
        return dict(cut_scores)