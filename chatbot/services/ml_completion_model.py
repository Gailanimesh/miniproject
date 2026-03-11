from dataclasses import dataclass

import numpy as np
from django.core.cache import cache
from django.utils import timezone

from chatbot.models import UserModelSnapshot
from timetable.models import ExamSubject, TimetableEntry
from users.models import UserProfile

DIFFICULTY_SCORE_MAP = {
    "easy": 0.2,
    "medium": 0.5,
    "hard": 0.85,
}

FEATURE_NAMES = [
    "duration_norm",
    "priority_norm",
    "days_until_exam_norm",
    "difficulty_score",
    "hour_norm",
    "weekday_norm",
    "preferred_time_match",
    "recent_miss_rate",
    "remaining_ratio",
]

MIN_HISTORICAL_SAMPLES = 24
MIN_MIXED_SAMPLES = 8
SYNTHETIC_SAMPLES = 160
MODEL_VERSION = 1
CACHE_TIMEOUT_SECONDS = 60 * 20
SNAPSHOT_MAX_AGE_HOURS = 6
RETRAIN_SAMPLE_GROWTH = 12


@dataclass
class TrainingSummary:
    source: str
    historical_samples: int
    synthetic_samples: int
    total_samples: int
    epochs: int
    train_loss: float
    val_loss: float
    val_accuracy: float
    early_stopped: bool
    regularization: float

    def to_dict(self):
        return {
            "source": self.source,
            "historical_samples": self.historical_samples,
            "synthetic_samples": self.synthetic_samples,
            "total_samples": self.total_samples,
            "epochs": self.epochs,
            "train_loss": round(self.train_loss, 6),
            "val_loss": round(self.val_loss, 6),
            "val_accuracy": round(self.val_accuracy, 6),
            "early_stopped": self.early_stopped,
            "regularization": self.regularization,
            "features": FEATURE_NAMES,
        }


class LogisticCompletionModel:
    def __init__(
        self,
        learning_rate=0.08,
        l2_lambda=0.02,
        max_epochs=260,
        patience=28,
        min_delta=1e-4,
    ):
        self.learning_rate = learning_rate
        self.l2_lambda = l2_lambda
        self.max_epochs = max_epochs
        self.patience = patience
        self.min_delta = min_delta

        self.weights = None
        self.bias = 0.0
        self.mean_ = None
        self.scale_ = None
        self.trained = False
        self.training_info = {
            "epochs": 0,
            "train_loss": 0.0,
            "val_loss": 0.0,
            "val_accuracy": 0.0,
            "early_stopped": False,
        }

    @staticmethod
    def _sigmoid(values):
        clipped = np.clip(values, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-clipped))

    @staticmethod
    def _binary_cross_entropy(targets, probs):
        eps = 1e-8
        return -np.mean(
            targets * np.log(probs + eps) + (1.0 - targets) * np.log(1.0 - probs + eps)
        )

    def fit(self, features, labels, random_seed=42):
        if len(features) < 6:
            return False

        labels = labels.astype(np.float64)
        unique_labels = np.unique(labels)
        if len(unique_labels) < 2:
            return False

        features = features.astype(np.float64)
        sample_count = len(features)
        rng = np.random.default_rng(random_seed)
        indices = np.arange(sample_count)
        rng.shuffle(indices)

        val_size = max(1, int(round(sample_count * 0.2)))
        if sample_count - val_size < 3:
            val_size = max(1, sample_count - 3)

        val_indices = indices[:val_size]
        train_indices = indices[val_size:]

        x_train = features[train_indices]
        y_train = labels[train_indices]
        x_val = features[val_indices]
        y_val = labels[val_indices]

        self.mean_ = x_train.mean(axis=0)
        self.scale_ = x_train.std(axis=0)
        self.scale_[self.scale_ < 1e-6] = 1.0

        x_train = (x_train - self.mean_) / self.scale_
        x_val = (x_val - self.mean_) / self.scale_

        self.weights = np.zeros(x_train.shape[1], dtype=np.float64)
        self.bias = 0.0

        best_weights = self.weights.copy()
        best_bias = self.bias
        best_val_loss = float("inf")
        stale_epochs = 0
        epoch = 0
        last_train_loss = 0.0

        for epoch in range(1, self.max_epochs + 1):
            train_probs = self._sigmoid(np.dot(x_train, self.weights) + self.bias)
            error = train_probs - y_train

            grad_weights = (np.dot(x_train.T, error) / len(x_train)) + self.l2_lambda * self.weights
            grad_bias = float(np.mean(error))

            self.weights -= self.learning_rate * grad_weights
            self.bias -= self.learning_rate * grad_bias

            train_probs = self._sigmoid(np.dot(x_train, self.weights) + self.bias)
            val_probs = self._sigmoid(np.dot(x_val, self.weights) + self.bias)

            train_loss = self._binary_cross_entropy(y_train, train_probs)
            train_loss += 0.5 * self.l2_lambda * float(np.sum(self.weights ** 2))
            val_loss = self._binary_cross_entropy(y_val, val_probs)
            last_train_loss = train_loss

            if val_loss + self.min_delta < best_val_loss:
                best_val_loss = val_loss
                best_weights = self.weights.copy()
                best_bias = self.bias
                stale_epochs = 0
            else:
                stale_epochs += 1
                if stale_epochs >= self.patience:
                    break

        self.weights = best_weights
        self.bias = best_bias

        final_val_probs = self._sigmoid(np.dot(x_val, self.weights) + self.bias)
        final_train_probs = self._sigmoid(np.dot(x_train, self.weights) + self.bias)
        final_train_loss = self._binary_cross_entropy(y_train, final_train_probs)
        final_train_loss += 0.5 * self.l2_lambda * float(np.sum(self.weights ** 2))
        final_val_loss = self._binary_cross_entropy(y_val, final_val_probs)
        final_val_accuracy = float(np.mean((final_val_probs >= 0.5) == y_val))

        self.training_info = {
            "epochs": epoch,
            "train_loss": float(final_train_loss),
            "val_loss": float(final_val_loss),
            "val_accuracy": final_val_accuracy,
            "early_stopped": stale_epochs >= self.patience,
        }
        self.trained = True
        return True

    def predict_proba(self, features):
        if not self.trained or self.weights is None:
            return np.array([0.5] * len(features), dtype=np.float64)

        x = features.astype(np.float64)
        x = (x - self.mean_) / self.scale_
        return self._sigmoid(np.dot(x, self.weights) + self.bias)


class UserCompletionRanker:
    def __init__(self, model, user_profile, exam_subjects, recent_miss_rate, summary):
        self.model = model
        self.user_profile = user_profile
        self.exam_subjects = list(exam_subjects)
        self.recent_miss_rate = float(recent_miss_rate)
        self.summary = summary

    def score_topic_slot(self, topic, start, end):
        vector = build_feature_vector(
            topic=topic,
            start=start,
            end=end,
            user_profile=self.user_profile,
            exam_subjects=self.exam_subjects,
            recent_miss_rate=self.recent_miss_rate,
            reference_date=start.date(),
        )
        probability = float(self.model.predict_proba(vector.reshape(1, -1))[0])
        return max(0.0, min(probability, 1.0))

    def metadata(self):
        return {
            "trained": True,
            "recent_miss_rate": round(self.recent_miss_rate, 6),
            "training": self.summary.to_dict(),
        }


def _cache_key_for_user(user_id):
    return f"completion_ranker:{MODEL_VERSION}:{user_id}"


def _snapshot_age_hours(snapshot):
    if not snapshot:
        return None
    delta = timezone.now() - snapshot.trained_at
    return delta.total_seconds() / 3600.0


def _estimate_historical_sample_count(user):
    return TimetableEntry.objects.filter(user=user, end__lt=timezone.now()).count()


def _build_summary_from_snapshot(snapshot):
    return TrainingSummary(
        source=snapshot.training_source,
        historical_samples=snapshot.historical_samples,
        synthetic_samples=snapshot.synthetic_samples,
        total_samples=snapshot.total_samples,
        epochs=snapshot.epochs,
        train_loss=snapshot.train_loss,
        val_loss=snapshot.val_loss,
        val_accuracy=snapshot.val_accuracy,
        early_stopped=False,
        regularization=snapshot.regularization,
    )


def _build_model_from_snapshot(snapshot):
    model = LogisticCompletionModel(l2_lambda=snapshot.regularization)

    weights = np.array(snapshot.weights or [], dtype=np.float64)
    mean_vector = np.array(snapshot.mean_vector or [], dtype=np.float64)
    scale_vector = np.array(snapshot.scale_vector or [], dtype=np.float64)

    if (
        len(weights) != len(FEATURE_NAMES)
        or len(mean_vector) != len(FEATURE_NAMES)
        or len(scale_vector) != len(FEATURE_NAMES)
    ):
        return None

    scale_vector[scale_vector < 1e-6] = 1.0

    model.weights = weights
    model.mean_ = mean_vector
    model.scale_ = scale_vector
    model.bias = float(snapshot.bias)
    model.trained = True
    model.training_info = {
        "epochs": int(snapshot.epochs),
        "train_loss": float(snapshot.train_loss),
        "val_loss": float(snapshot.val_loss),
        "val_accuracy": float(snapshot.val_accuracy),
        "early_stopped": False,
    }
    return model


def _persist_snapshot(user, model, summary):
    UserModelSnapshot.objects.update_or_create(
        user=user,
        defaults={
            "model_version": MODEL_VERSION,
            "feature_names": FEATURE_NAMES,
            "weights": [float(item) for item in model.weights.tolist()],
            "mean_vector": [float(item) for item in model.mean_.tolist()],
            "scale_vector": [float(item) for item in model.scale_.tolist()],
            "bias": float(model.bias),
            "training_source": summary.source,
            "historical_samples": summary.historical_samples,
            "synthetic_samples": summary.synthetic_samples,
            "total_samples": summary.total_samples,
            "epochs": summary.epochs,
            "train_loss": summary.train_loss,
            "val_loss": summary.val_loss,
            "val_accuracy": summary.val_accuracy,
            "regularization": summary.regularization,
        },
    )


def _should_retrain(snapshot, historical_count):
    if snapshot is None:
        return True
    if snapshot.model_version != MODEL_VERSION:
        return True
    age_hours = _snapshot_age_hours(snapshot)
    if age_hours is None or age_hours > SNAPSHOT_MAX_AGE_HOURS:
        return True
    if historical_count >= snapshot.historical_samples + RETRAIN_SAMPLE_GROWTH:
        return True
    if snapshot.training_source == "synthetic" and historical_count >= MIN_MIXED_SAMPLES:
        return True
    return False


def _attempt_build_ranker_from_snapshot(user, user_profile, exam_subjects, recent_miss_rate):
    snapshot = UserModelSnapshot.objects.filter(user=user).first()
    historical_count = _estimate_historical_sample_count(user)
    if _should_retrain(snapshot, historical_count):
        return None, None

    model = _build_model_from_snapshot(snapshot)
    if model is None:
        return None, None

    summary = _build_summary_from_snapshot(snapshot)
    ranker = UserCompletionRanker(
        model=model,
        user_profile=user_profile,
        exam_subjects=exam_subjects,
        recent_miss_rate=recent_miss_rate,
        summary=summary,
    )
    metadata = ranker.metadata()
    metadata["from_snapshot"] = True
    metadata["snapshot_age_hours"] = round(_snapshot_age_hours(snapshot) or 0.0, 4)
    return ranker, metadata


def _topic_exam_context(topic_name, exam_subjects, reference_date):
    lowered_name = (topic_name or "").lower()
    best_days = 90
    best_difficulty = DIFFICULTY_SCORE_MAP["medium"]

    for subject in exam_subjects:
        subject_name = (subject.name or "").lower()
        if subject_name not in lowered_name and lowered_name not in subject_name:
            continue

        days_left = max((subject.exam_date - reference_date).days, 0)
        difficulty = DIFFICULTY_SCORE_MAP.get((subject.difficulty or "medium").lower(), 0.5)

        if days_left < best_days:
            best_days = days_left
            best_difficulty = difficulty

    return best_days, best_difficulty


def _preferred_time_match(preferred_study_time, dt):
    pref = (preferred_study_time or "").strip().lower()
    if not pref:
        return 0.5

    hour = dt.hour
    bucket_map = {
        "morning": range(5, 12),
        "afternoon": range(12, 17),
        "evening": range(17, 22),
        "night": tuple([22, 23, 0, 1, 2, 3, 4]),
    }

    for key, valid_hours in bucket_map.items():
        if key in pref:
            return 1.0 if hour in valid_hours else 0.0

    return 0.5


def build_feature_vector(
    topic,
    start,
    end,
    user_profile,
    exam_subjects,
    recent_miss_rate,
    reference_date=None,
):
    duration_minutes = max(1, int((end - start).total_seconds() // 60))
    priority = max(1, min(10, int(topic.priority or 1)))

    estimated = max(1, int(topic.estimated_minutes or 1))
    completed = max(0, int(topic.completed_minutes or 0))
    remaining = max(0, estimated - completed)

    reference_date = reference_date or timezone.now().date()
    days_until_exam, difficulty_score = _topic_exam_context(
        topic_name=topic.name,
        exam_subjects=exam_subjects,
        reference_date=reference_date,
    )

    return np.array(
        [
            min(duration_minutes, 180) / 180.0,
            priority / 10.0,
            min(days_until_exam, 90) / 90.0,
            float(difficulty_score),
            start.hour / 23.0,
            start.weekday() / 6.0,
            _preferred_time_match(getattr(user_profile, "preferred_study_time", ""), start),
            max(0.0, min(float(recent_miss_rate), 1.0)),
            min(remaining / estimated, 1.0),
        ],
        dtype=np.float64,
    )


def _build_historical_dataset(user, user_profile, exam_subjects):
    now = timezone.now()
    entries = list(
        TimetableEntry.objects.filter(user=user, end__lt=now)
        .select_related("topic")
        .order_by("start")[:320]
    )

    if not entries:
        return np.empty((0, len(FEATURE_NAMES))), np.empty((0,)), 0

    features = []
    labels = []
    misses = 0
    observed = 0

    for entry in entries:
        miss_rate = (misses / observed) if observed > 0 else 0.25
        vector = build_feature_vector(
            topic=entry.topic,
            start=entry.start,
            end=entry.end,
            user_profile=user_profile,
            exam_subjects=exam_subjects,
            recent_miss_rate=miss_rate,
            reference_date=entry.start.date(),
        )
        features.append(vector)
        label = 1.0 if entry.done else 0.0
        labels.append(label)

        observed += 1
        if not entry.done:
            misses += 1

    return np.array(features, dtype=np.float64), np.array(labels, dtype=np.float64), len(entries)


def _build_synthetic_dataset(sample_count, random_seed):
    rng = np.random.default_rng(random_seed)

    duration_norm = rng.uniform(0.12, 1.0, sample_count)
    priority_norm = rng.uniform(0.1, 1.0, sample_count)
    days_until_exam_norm = rng.uniform(0.0, 1.0, sample_count)
    difficulty_score = rng.uniform(0.2, 0.95, sample_count)
    hour_norm = rng.uniform(0.0, 1.0, sample_count)
    weekday_norm = rng.uniform(0.0, 1.0, sample_count)
    preferred_time_match = rng.binomial(1, 0.45, sample_count).astype(np.float64)
    recent_miss_rate = rng.uniform(0.0, 0.95, sample_count)
    remaining_ratio = rng.uniform(0.05, 1.0, sample_count)

    features = np.column_stack(
        [
            duration_norm,
            priority_norm,
            days_until_exam_norm,
            difficulty_score,
            hour_norm,
            weekday_norm,
            preferred_time_match,
            recent_miss_rate,
            remaining_ratio,
        ]
    )

    latent = (
        1.6
        - 1.6 * duration_norm
        + 1.4 * priority_norm
        - 0.6 * days_until_exam_norm
        - 0.8 * difficulty_score
        - 1.35 * recent_miss_rate
        + 0.9 * preferred_time_match
        + 0.35 * remaining_ratio
    )

    late_hours_penalty = np.clip(hour_norm - 0.84, 0.0, None) * 2.0
    weekend_penalty = np.clip(weekday_norm - 0.80, 0.0, None) * 1.5
    latent -= (late_hours_penalty + weekend_penalty)

    probs = 1.0 / (1.0 + np.exp(-latent))
    labels = rng.binomial(1, probs).astype(np.float64)

    if len(np.unique(labels)) < 2:
        labels = (probs >= np.median(probs)).astype(np.float64)

    return features, labels


def _recent_miss_rate(user):
    recent_entries = list(
        TimetableEntry.objects.filter(user=user, end__lt=timezone.now())
        .order_by("-end")[:20]
    )
    if not recent_entries:
        return 0.25

    misses = sum(1 for entry in recent_entries if not entry.done)
    return misses / len(recent_entries)


def build_user_completion_ranker(user):
    user_profile = UserProfile.objects.filter(user=user).first()
    exam_subjects = list(ExamSubject.objects.filter(user=user))
    recent_miss_rate = _recent_miss_rate(user)

    cache_key = _cache_key_for_user(user.id)
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        snapshot_like = type("SnapshotData", (), cached)
        model = _build_model_from_snapshot(snapshot_like)
        if model is not None:
            summary = _build_summary_from_snapshot(snapshot_like)
            ranker = UserCompletionRanker(
                model=model,
                user_profile=user_profile,
                exam_subjects=exam_subjects,
                recent_miss_rate=recent_miss_rate,
                summary=summary,
            )
            metadata = ranker.metadata()
            metadata["from_cache"] = True
            return ranker, metadata

    snapshot_ranker, snapshot_meta = _attempt_build_ranker_from_snapshot(
        user=user,
        user_profile=user_profile,
        exam_subjects=exam_subjects,
        recent_miss_rate=recent_miss_rate,
    )
    if snapshot_ranker is not None:
        snapshot = UserModelSnapshot.objects.filter(user=user).first()
        if snapshot:
            cache.set(
                cache_key,
                {
                    "model_version": snapshot.model_version,
                    "weights": snapshot.weights,
                    "mean_vector": snapshot.mean_vector,
                    "scale_vector": snapshot.scale_vector,
                    "bias": snapshot.bias,
                    "training_source": snapshot.training_source,
                    "historical_samples": snapshot.historical_samples,
                    "synthetic_samples": snapshot.synthetic_samples,
                    "total_samples": snapshot.total_samples,
                    "epochs": snapshot.epochs,
                    "train_loss": snapshot.train_loss,
                    "val_loss": snapshot.val_loss,
                    "val_accuracy": snapshot.val_accuracy,
                    "regularization": snapshot.regularization,
                    "trained_at": snapshot.trained_at,
                },
                timeout=CACHE_TIMEOUT_SECONDS,
            )
        snapshot_meta["from_cache"] = False
        return snapshot_ranker, snapshot_meta

    historical_x, historical_y, historical_samples = _build_historical_dataset(
        user=user,
        user_profile=user_profile,
        exam_subjects=exam_subjects,
    )

    historical_has_variance = len(np.unique(historical_y)) > 1 if historical_samples else False
    synthetic_x = np.empty((0, len(FEATURE_NAMES)), dtype=np.float64)
    synthetic_y = np.empty((0,), dtype=np.float64)

    if historical_samples >= MIN_HISTORICAL_SAMPLES and historical_has_variance:
        source = "historical"
        train_x = historical_x
        train_y = historical_y
    elif historical_samples >= MIN_MIXED_SAMPLES and historical_has_variance:
        source = "hybrid"
        synthetic_x, synthetic_y = _build_synthetic_dataset(
            sample_count=SYNTHETIC_SAMPLES,
            random_seed=int(user.id or 0) + 41,
        )
        train_x = np.vstack([historical_x, synthetic_x])
        train_y = np.concatenate([historical_y, synthetic_y])
    else:
        source = "synthetic"
        synthetic_x, synthetic_y = _build_synthetic_dataset(
            sample_count=SYNTHETIC_SAMPLES,
            random_seed=int(user.id or 0) + 41,
        )
        train_x = synthetic_x
        train_y = synthetic_y

    model = LogisticCompletionModel()
    trained = model.fit(train_x, train_y, random_seed=int(user.id or 0) + 7)

    if not trained:
        return None, {
            "trained": False,
            "reason": "insufficient_class_variation",
            "source": source,
            "historical_samples": historical_samples,
            "synthetic_samples": len(synthetic_y),
            "from_cache": False,
            "from_snapshot": False,
        }

    info = model.training_info
    summary = TrainingSummary(
        source=source,
        historical_samples=historical_samples,
        synthetic_samples=len(synthetic_y),
        total_samples=len(train_y),
        epochs=int(info.get("epochs", 0)),
        train_loss=float(info.get("train_loss", 0.0)),
        val_loss=float(info.get("val_loss", 0.0)),
        val_accuracy=float(info.get("val_accuracy", 0.0)),
        early_stopped=bool(info.get("early_stopped", False)),
        regularization=model.l2_lambda,
    )

    ranker = UserCompletionRanker(
        model=model,
        user_profile=user_profile,
        exam_subjects=exam_subjects,
        recent_miss_rate=recent_miss_rate,
        summary=summary,
    )
    _persist_snapshot(user=user, model=model, summary=summary)
    cache.set(
        cache_key,
        {
            "model_version": MODEL_VERSION,
            "weights": [float(item) for item in model.weights.tolist()],
            "mean_vector": [float(item) for item in model.mean_.tolist()],
            "scale_vector": [float(item) for item in model.scale_.tolist()],
            "bias": float(model.bias),
            "training_source": summary.source,
            "historical_samples": summary.historical_samples,
            "synthetic_samples": summary.synthetic_samples,
            "total_samples": summary.total_samples,
            "epochs": summary.epochs,
            "train_loss": summary.train_loss,
            "val_loss": summary.val_loss,
            "val_accuracy": summary.val_accuracy,
            "regularization": summary.regularization,
            "trained_at": timezone.now(),
        },
        timeout=CACHE_TIMEOUT_SECONDS,
    )
    metadata = ranker.metadata()
    metadata["from_cache"] = False
    metadata["from_snapshot"] = False
    return ranker, metadata
