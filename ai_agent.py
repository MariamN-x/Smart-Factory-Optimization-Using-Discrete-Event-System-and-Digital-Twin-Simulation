#!/usr/bin/env python3
"""
AI Agent for 6-Station 3D Printer Production Line
Production-ready CLI tool for training and generating recommendations
Handles both tabular data AND nested configuration JSON (e.g., opt.json)
"""
import argparse
import json
import os
import sys
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from joblib import dump, load
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    classification_report,
    confusion_matrix
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer


# Constants
RANDOM_STATE = 42
LABEL_COLUMN = 'label'
LABEL_ALTERNATIVES = ['target', 'class', 'status',
                      'state', 'event_label', 'y', 'output']
CLASS_NAMES = ['normal', 'bottleneck', 'plc_comm_issue',
               'quality_risk', 'maintenance_needed']
DEFAULT_MODEL_PATH = 'line_model.joblib'

# Auto-labeling rules for tabular data (priority order)
AUTO_LABEL_RULES_TABULAR = [
    ('maintenance_needed', lambda row:
        (row.get('downtime_sec', 0) > 300) or
        (row.get('downtime', 0) > 300)),
    ('quality_risk', lambda row:
        (row.get('defect_rate', 0) > 0.05) or
        (isinstance(row.get('defect_count'), (int, float)) and row.get('defect_count', 0) > 5)),
    ('plc_comm_issue', lambda row:
        (row.get('plc_command_latency_ms', 0) > 100) or
        (row.get('heartbeat_ok') is False) or
        (row.get('plc_comm_ok') is False)),
    ('bottleneck', lambda row:
        (row.get('queue_len', 0) > 10) or
        (row.get('backlog', 0) > 10)),
]

# Auto-labeling rules for station configuration data
AUTO_LABEL_RULES_STATION = [
    ('bottleneck', lambda row:
        (row.get('criticality') == 'bottleneck_candidate') or
        (row.get('cycle_time_s', 0) > 15) or
        (row.get('setup_time_s', 0) > 200)),
    ('maintenance_needed', lambda row:
        (row.get('failure_rate', 0) > 0.04) or
        (row.get('mttr_s', 0) > 50) or
        (row.get('mtbf_h', 0) < 25)),
    ('plc_comm_issue', lambda row:
        (row.get('requires_operator') is False and row.get('failure_rate', 0) > 0.025)),
    ('quality_risk', lambda row:
        (row.get('criticality') == 'high' and row.get('parallel_machines', 1) == 1)),
]

# Recommendation templates
RECOMMENDATION_TEMPLATES = {
    'normal': "Production line operating normally. No action required.",
    'bottleneck': "Bottleneck detected at station {station_id}. Consider increasing throughput or investigating upstream delays.",
    'plc_comm_issue': "PLC communication issue detected. Check network connectivity and PLC master-slave synchronization.",
    'quality_risk': "Quality risk detected. Inspect recent prints for defects and calibrate printers.",
    'maintenance_needed': "Maintenance required. Schedule downtime for station {station_id} to prevent failure.",
}

# Rule-based hint messages
HINT_MESSAGES = {
    'maintenance_needed': "High downtime/failure rate observed.",
    'quality_risk': "High defect rate or single-point-of-failure station.",
    'plc_comm_issue': "PLC communication latency or heartbeat failure detected.",
    'bottleneck': "High cycle time or queue length observed.",
}


def validate_json_extension(filepath: str) -> None:
    """Validate that input file has .json extension."""
    if not filepath.lower().endswith('.json'):
        raise ValueError(
            f"Invalid file format. Only .json files are accepted. "
            f"Received: {filepath}"
        )


def load_json_file(filepath: str) -> pd.DataFrame:
    """Load and normalize JSON data (handles both array and single object)."""
    validate_json_extension(filepath)

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in {filepath}: {e}")

    # Normalize nested structures
    if isinstance(data, dict):
        data = [data]
    return pd.json_normalize(data, sep='.')


def detect_station_configuration(df: pd.DataFrame) -> bool:
    """Detect if flattened JSON contains station configuration structure."""
    station_patterns = ['stations.S',
                        'stations.station', 'stations.1', 'stations.2']
    return any(col.startswith('stations.') for col in df.columns) and len(df) == 1


def transform_station_config_to_examples(df: pd.DataFrame) -> pd.DataFrame:
    """
    Transform nested station configuration into station-level training examples.
    Converts columns like 'stations.S1.cycle_time_s' into rows with station_id.
    """
    record = df.iloc[0].to_dict()

    # Extract station keys (e.g., 'stations.S1', 'stations.S2', etc.)
    station_prefixes = sorted(set(
        col.split('.')[0] + '.' + col.split('.')[1]
        for col in record.keys() if col.startswith('stations.')
    ))

    examples = []
    for prefix in station_prefixes:
        # Extract all fields under this station prefix
        station_data = {
            k.replace(prefix + '.', ''): v
            for k, v in record.items()
            if k.startswith(prefix + '.')
        }

        # Add station identifier
        station_id = prefix.split('.')[-1]
        station_data['station_id'] = station_id

        # Add top-level metadata relevant to all stations
        for key in ['simulation_time_s', 'simulation_time_h']:
            if key in record:
                station_data[f'total_{key}'] = record[key]

        examples.append(station_data)

    return pd.DataFrame(examples)


def make_hashable_for_duplication(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert non-hashable types (dict/list/set/tuple) to JSON strings 
    to enable safe duplicate detection.
    """
    df_copy = df.copy()
    for col in df_copy.columns:
        mask = df_copy[col].apply(
            lambda x: isinstance(x, (dict, list, set, tuple)))
        if mask.any():
            df_copy.loc[mask, col] = df_copy.loc[mask, col].apply(
                lambda x: json.dumps(x, sort_keys=True, default=str)
            )
    return df_copy


def find_label_column(df: pd.DataFrame) -> Optional[str]:
    """Find label column or its alternatives in the dataframe."""
    if LABEL_COLUMN in df.columns:
        return LABEL_COLUMN
    for alt in LABEL_ALTERNATIVES:
        if alt in df.columns:
            return alt
    return None


def auto_label_dataframe(df: pd.DataFrame, is_station_config: bool = False) -> pd.DataFrame:
    """
    Generate weak labels using rule-based heuristics.
    Uses different rules for tabular data vs station configuration.
    """
    df = df.copy()
    df['label'] = 'normal'  # Default label

    rules = AUTO_LABEL_RULES_STATION if is_station_config else AUTO_LABEL_RULES_TABULAR

    # Track which rows have been labeled
    labeled_mask = pd.Series(False, index=df.index)

    for label_name, condition_fn in rules:
        for idx in df[~labeled_mask].index:
            row = df.loc[idx]
            try:
                if condition_fn(row):
                    df.at[idx, 'label'] = label_name
                    labeled_mask.at[idx] = True
            except Exception:
                continue

    return df


def ensure_json_extension(filepath: str) -> str:
    """Ensure output file has .json extension."""
    if not filepath.lower().endswith('.json'):
        base = os.path.splitext(filepath)[0]
        return f"{base}.json"
    return filepath


def ensure_joblib_extension(filepath: str) -> str:
    """Ensure model file has .joblib extension."""
    if not filepath.lower().endswith('.joblib'):
        base = os.path.splitext(filepath)[0]
        return f"{base}.joblib"
    return filepath


def create_pipeline(numeric_features: List[str],
                    categorical_features: List[str]) -> Pipeline:
    """Create sklearn pipeline with preprocessing and classifier."""
    numeric_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler())
    ])

    categorical_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='most_frequent')),
        ('encoder', OneHotEncoder(handle_unknown='ignore', sparse_output=False))
    ])

    preprocessor = ColumnTransformer(transformers=[
        ('num', numeric_transformer, numeric_features),
        ('cat', categorical_transformer, categorical_features)
    ])

    classifier = RandomForestClassifier(
        n_estimators=100,
        max_depth=15,
        min_samples_split=5,
        class_weight='balanced',
        random_state=RANDOM_STATE,
        n_jobs=-1
    )

    return Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', classifier)
    ])


def generate_recommendation(
    prediction: str,
    row: Dict[str, Any],
    station_id: Optional[Any],
    timestamp: Optional[Any]
) -> str:
    """
    Generate human-readable recommendation with rule-based hints.
    """
    # Base recommendation from prediction
    base_template = RECOMMENDATION_TEMPLATES.get(
        prediction,
        f"Unknown state: {prediction}. Investigate immediately."
    )

    # Format station_id into template if placeholder exists
    if '{station_id}' in base_template:
        station_str = str(station_id) if station_id is not None else 'unknown'
        base_template = base_template.format(station_id=station_str)

    # Generate rule-based hints
    hints = []
    for label_name, condition_fn in AUTO_LABEL_RULES_TABULAR + AUTO_LABEL_RULES_STATION:
        try:
            if condition_fn(row):
                hints.append(HINT_MESSAGES.get(
                    label_name, f"Rule triggered: {label_name}"))
        except Exception:
            continue

    # Combine base recommendation with hints
    if hints:
        return f"{base_template} Hint: {'; '.join(hints)}"
    return base_template


def train_command(args: argparse.Namespace) -> None:
    """Execute training command."""
    # Load and validate data
    df = load_json_file(args.data)

    # Initialize validation report
    validation_report = {
        'row_count_before': len(df),
        'missing_required_columns': [],
        'rows_dropped_missing_label': 0,
        'duplicate_rows': 0,
        'null_ratio_by_column': {},
        'row_count_after': 0,
        'data_type': 'tabular'
    }

    # Detect and transform station configuration data
    is_station_config = detect_station_configuration(df)
    if is_station_config:
        print("Detected station configuration JSON. Transforming into station-level examples...")
        df = transform_station_config_to_examples(df)
        validation_report['data_type'] = 'station_configuration'
        validation_report['stations_extracted'] = len(df)
        print(f"Extracted {len(df)} station examples from configuration")

    # Handle label column
    label_col = find_label_column(df)
    if label_col is None:
        if args.auto_label:
            print(
                "Label column not found. Generating weak labels using --auto-label rules...")
            df = auto_label_dataframe(df, is_station_config=is_station_config)
            label_col = 'label'
        else:
            raise ValueError(
                f"Required label column not found. Searched for: '{LABEL_COLUMN}' "
                f"and alternatives {LABEL_ALTERNATIVES}. Use --auto-label to generate labels automatically."
            )
    else:
        # Rename found label column to standard name
        if label_col != LABEL_COLUMN:
            df = df.rename(columns={label_col: LABEL_COLUMN})
            label_col = LABEL_COLUMN

    # Drop rows with missing labels (if not auto-labeled)
    if not args.auto_label:
        initial_count = len(df)
        df = df.dropna(subset=[LABEL_COLUMN])
        validation_report['rows_dropped_missing_label'] = initial_count - \
            len(df)

    # Handle duplicates safely
    df_hashable = make_hashable_for_duplication(df)
    dup_mask = df_hashable.duplicated()
    validation_report['duplicate_rows'] = int(dup_mask.sum())
    df = df[~dup_mask].reset_index(drop=True)

    # Compute null ratios
    validation_report['null_ratio_by_column'] = df.isnull().mean().to_dict()
    validation_report['row_count_after'] = len(df)

    # Final validation checks
    if len(df) == 0:
        raise ValueError(
            "No data remaining after cleaning. Check input file quality.")

    if df[LABEL_COLUMN].nunique() < 2:
        raise ValueError(
            f"Insufficient class diversity. Found only {df[LABEL_COLUMN].nunique()} unique class(es). "
            "Need at least 2 classes for classification."
        )

    # Prepare features and target
    y = df[LABEL_COLUMN]
    X = df.drop(columns=[LABEL_COLUMN])

    # Identify feature types
    numeric_features = X.select_dtypes(
        include=['int64', 'float64']).columns.tolist()
    categorical_features = X.select_dtypes(
        include=['object', 'category', 'bool']).columns.tolist()

    # Handle columns with mixed types (treat as categorical)
    mixed_type_cols = [
        col for col in X.columns
        if col not in numeric_features and col not in categorical_features
    ]
    if mixed_type_cols:
        categorical_features.extend(mixed_type_cols)
        print(
            f"Warning: Treating mixed-type columns as categorical: {mixed_type_cols}")

    # Create and configure pipeline
    pipeline = create_pipeline(numeric_features, categorical_features)

    # Train-test split with stratification fallback
    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y,
            test_size=args.test_size,
            random_state=RANDOM_STATE,
            stratify=y
        )
    except ValueError as e:
        print(
            f"Warning: Stratified split failed ({e}). Falling back to non-stratified split.")
        X_train, X_test, y_train, y_test = train_test_split(
            X, y,
            test_size=args.test_size,
            random_state=RANDOM_STATE
        )

    # Train model
    pipeline.fit(X_train, y_train)

    # Evaluate on test set
    y_pred = pipeline.predict(X_test)

    # Compute metrics
    metrics = {
        'f1_macro': float(f1_score(y_test, y_pred, average='macro', zero_division=0)),
        'f1_weighted': float(f1_score(y_test, y_pred, average='weighted', zero_division=0)),
        'precision_macro': float(precision_score(y_test, y_pred, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(y_test, y_pred, average='macro', zero_division=0)),
        'classification_report': classification_report(y_test, y_pred, output_dict=True, zero_division=0),
        'confusion_matrix': confusion_matrix(y_test, y_pred).tolist(),
        'train_size': len(X_train),
        'test_size': len(X_test),
        'class_distribution_train': y_train.value_counts().to_dict(),
        'class_distribution_test': y_test.value_counts().to_dict(),
        'feature_columns': {
            'numeric': numeric_features,
            'categorical': categorical_features
        },
        'validation_report': validation_report
    }

    # Ensure correct output extensions
    model_out = ensure_joblib_extension(args.model_out)
    metrics_out = ensure_json_extension(args.metrics_out)

    # Save artifacts
    dump(pipeline, model_out)

    with open(metrics_out, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, default=str)

    # Print sample predictions
    print("\nSample test predictions (true -> predicted):")
    sample_size = min(5, len(y_test))
    sample_indices = np.random.choice(
        len(y_test), size=sample_size, replace=False)
    for idx in sample_indices:
        true_val = y_test.iloc[idx]
        pred_val = y_pred[idx]
        station_id = X_test.iloc[idx].get(
            'station_id', 'N/A') if 'station_id' in X_test.columns else 'N/A'
        print(f"  Station {station_id:<5} {true_val:<20} -> {pred_val}")

    print(f"\nModel saved to: {model_out}")
    print(f"Metrics saved to: {metrics_out}")
    print(f"Training complete. Test set F1 (macro): {metrics['f1_macro']:.4f}")


def recommend_command(args: argparse.Namespace) -> None:
    """Execute recommendation command."""
    # Determine model path (positional arg or default)
    model_path = args.model_path if args.model_path else DEFAULT_MODEL_PATH

    # Load model
    try:
        model = load(model_path)
    except FileNotFoundError:
        raise ValueError(
            f"Model file not found: {model_path}\n"
            f"Train a model first: python ai_agent.py train --data training.json"
        )
    except Exception as e:
        raise ValueError(f"Failed to load model from {model_path}: {e}")

    # Load input data
    df = load_json_file(args.input)

    # Detect and transform station configuration data for recommendations
    if detect_station_configuration(df):
        print("Detected station configuration JSON. Transforming for recommendations...")
        df = transform_station_config_to_examples(df)
        print(f"Generated recommendations for {len(df)} stations")

    # Remove label columns if present (not needed for prediction)
    cols_to_drop = [col for col in [LABEL_COLUMN] +
                    LABEL_ALTERNATIVES if col in df.columns]
    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)
        print(f"Dropped label columns from input: {cols_to_drop}")

    # Get expected features from trained model
    try:
        expected_features = model.named_steps['preprocessor'].feature_names_in_
    except AttributeError:
        # Fallback: use all available columns
        expected_features = df.columns.tolist()
        print("Warning: Could not determine expected features from model. Using all input columns.")

    # Align input columns with model expectations
    missing_cols = set(expected_features) - set(df.columns)
    extra_cols = set(df.columns) - set(expected_features)

    if missing_cols:
        print(
            f"Warning: Adding {len(missing_cols)} missing columns as NaN: {sorted(missing_cols)[:5]}...")
        for col in missing_cols:
            df[col] = np.nan

    if extra_cols:
        print(
            f"Warning: Dropping {len(extra_cols)} unexpected columns: {sorted(extra_cols)[:5]}...")
        df = df.drop(columns=extra_cols)

    # Reorder columns to match training order
    df = df[expected_features]

    # Generate predictions
    predictions = model.predict(df)

    # Identify station_id and timestamp columns
    station_id_col = next(
        (col for col in ['station_id', 'station',
         'stationid', 'stationId'] if col in df.columns),
        None
    )
    timestamp_col = next(
        (col for col in ['timestamp', 'time',
         'ts', 'datetime'] if col in df.columns),
        None
    )

    # Generate recommendations
    recommendations = []
    print("\nGenerated Recommendations:")
    print("=" * 80)

    for idx, (pred, row_dict) in enumerate(zip(predictions, df.to_dict('records'))):
        station_id = row_dict.get(station_id_col) if station_id_col else None
        timestamp = row_dict.get(timestamp_col) if timestamp_col else None

        # Generate recommendation text
        rec_text = generate_recommendation(
            pred, row_dict, station_id, timestamp)

        # Build recommendation item
        rec_item = {
            'index': idx,
            'prediction': str(pred),
            'recommendation': rec_text,
            'station_id': station_id,
            'timestamp': timestamp
        }
        recommendations.append(rec_item)

        # Print to terminal
        print(f"\n[Station {station_id if station_id is not None else 'N/A'}]")
        if timestamp:
            print(f"  Timestamp: {timestamp}")
        print(f"  Prediction: {pred}")
        print(f"  Recommendation: {rec_text}")

    print("\n" + "=" * 80)

    # Save recommendations to JSON
    output_path = ensure_json_extension(args.output)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(recommendations, f, indent=2, default=str)

    print(f"\nRecommendations saved to: {output_path}")
    print(f"Total recommendations generated: {len(recommendations)}")


def main() -> None:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="AI Agent for 6-Station 3D Printer Production Line\n"
                    "Trains on BOTH tabular data AND nested configuration JSON (e.g., opt.json)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Train command
    train_parser = subparsers.add_parser(
        'train', help='Train production line anomaly detection model')
    train_parser.add_argument(
        '--data',
        type=str,
        required=True,
        help='Path to training data JSON file (tabular array OR nested configuration like opt.json)'
    )
    train_parser.add_argument(
        '--model-out',
        type=str,
        default=DEFAULT_MODEL_PATH,
        help=f'Path to save trained model (default: {DEFAULT_MODEL_PATH})'
    )
    train_parser.add_argument(
        '--metrics-out',
        type=str,
        default='metrics.json',
        help='Path to save evaluation metrics (will use .json extension)'
    )
    train_parser.add_argument(
        '--test-size',
        type=float,
        default=0.2,
        help='Test split fraction (default: 0.2)'
    )
    train_parser.add_argument(
        '--auto-label',
        action='store_true',
        help='Auto-generate labels using rule-based heuristics when label column is missing'
    )

    # Recommend command (model path is positional, not a flag)
    recommend_parser = subparsers.add_parser(
        'recommend',
        help='Generate production line recommendations',
        description='Generate recommendations using trained model.\n'
                    'MODEL_PATH is optional (defaults to line_model.joblib).'
    )
    recommend_parser.add_argument(
        'model_path',
        type=str,
        nargs='?',  # Optional positional argument
        default=None,
        help=f'Path to trained model (default: {DEFAULT_MODEL_PATH})'
    )
    recommend_parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to input data JSON file for recommendations (tabular OR configuration)'
    )
    recommend_parser.add_argument(
        '--output',
        type=str,
        default='recommendations.json',
        help='Path to save recommendations (will use .json extension)'
    )

    args = parser.parse_args()

    try:
        if args.command == 'train':
            train_command(args)
        elif args.command == 'recommend':
            recommend_command(args)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
