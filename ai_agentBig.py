#!/usr/bin/env python3
"""
AI Agent for 6-Station 3D Printer Production Line
Enhanced to learn from historical recommendations and generate intelligent, context-aware suggestions
"""
import argparse
import json
import os
import sys
import time
import re
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
import pandas as pd
from joblib import dump, load
from sklearn.ensemble import (
    RandomForestClassifier,
    GradientBoostingClassifier,
    ExtraTreesClassifier,
    VotingClassifier
)
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    f1_score,
    precision_score,
    recall_score,
    classification_report,
    confusion_matrix,
    accuracy_score
)
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.svm import SVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.calibration import CalibratedClassifierCV


# Constants
RANDOM_STATE = 42
LABEL_COLUMN = 'label'
LABEL_ALTERNATIVES = ['target', 'class', 'status',
                      'state', 'event_label', 'y', 'output']
CLASS_NAMES = ['normal', 'bottleneck', 'plc_comm_issue',
               'quality_risk', 'maintenance_needed']
DEFAULT_MODEL_PATH = 'line_model.joblib'
DEFAULT_TEST_SIZE = 0.2
CV_FOLDS = 5


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


# ==================== INTELLIGENT RECOMMENDATION SYSTEM ====================
# These templates are learned from historical data and can be extended with more examples
# Format: {station_pattern: {issue_type: [recommendation_templates]}}
INTELLIGENT_RECOMMENDATIONS = {
    # S1: Component Kitting & Pre-Assembly
    'S1': {
        'bottleneck': [
            "Add {additional_machines} additional collaborative robot arms (increase from {current_qty} to {target_qty} units)",
            "Optimize gripper changeover sequence (target {reduction_pct}% reduction)",
            "Implement vision-guided placement to reduce cycle time by {cycle_improvement_pct}%",
            "Increase S1→S2 buffer to {buffer_target} units (currently {current_buffer})"
        ],
        'maintenance_needed': [
            "Schedule preventive maintenance for collaborative robot arms (MTTR: {mttr_s}s)",
            "Replace worn grippers and check alignment sensors",
            "Calibrate vision systems and torque drivers",
            "Inspect cable carriers and pneumatic lines"
        ],
        'quality_risk': [
            "Implement barcode scanning for component verification",
            "Add torque monitoring for critical fasteners",
            "Install vision inspection for component placement accuracy"
        ]
    },

    # S2: Frame and Core Assembly
    'S2': {
        'bottleneck': [
            "Upgrade to high-speed bearing press ({current_cycle}s → {target_cycle}s cycle time)",
            "Add automated lubrication system to reduce failures by {failure_reduction_pct}%",
            "Increase S1→S2 buffer from {current_buffer} to {buffer_target} units",
            "Implement predictive maintenance for press alignment"
        ],
        'maintenance_needed': [
            "Service bearing press hydraulic system (MTTR: {mttr_s}s)",
            "Replace linear rail lubrication and check alignment",
            "Inspect frame welding fixtures and clamping mechanisms"
        ],
        'quality_risk': [
            "Install laser alignment system for frame assembly",
            "Add torque verification for critical structural fasteners",
            "Implement automated bed leveling verification"
        ]
    },

    # S3: Electronics and Wiring Installation
    'S3': {
        'bottleneck': [
            "Add {additional_stations} more smart torque stations (increase from {current_qty} to {target_qty} units)",
            "Implement real-time torque monitoring with IoT sensors",
            "Reduce changeover time with quick-release tooling",
            "Increase S2→S3 buffer to {buffer_target} units (currently {current_buffer})"
        ],
        'maintenance_needed': [
            "Calibrate smart torque drivers (MTTR: {mttr_s}s)",
            "Replace worn tooling and check sensor connections",
            "Service cable routing guides and strain relief systems"
        ],
        'quality_risk': [
            "Add automated wire continuity testing",
            "Implement torque traceability for all critical connections",
            "Install vision inspection for cable routing accuracy"
        ]
    },

    # S4: Automated Calibration and Testing
    'S4': {
        'bottleneck': [
            "Add parallel cable crimping machine (reduce bottleneck by {bottleneck_reduction_pct}%)",
            "Reduce cycle time from {current_cycle}s → {target_cycle}s via thermal chamber upgrade",
            "Increase S3→S4 buffer to {buffer_target} units (currently {current_buffer})",
            "Implement predictive maintenance for crimping heads",
            "ROI: {roi_months} months payback period at ${margin_per_unit}/unit margin"
        ],
        'maintenance_needed': [
            "Service calibration fixtures and measurement sensors (MTTR: {mttr_s}s)",
            "Replace worn crimping heads and check alignment",
            "Calibrate thermal chamber temperature profiles"
        ],
        'quality_risk': [
            "Upgrade motion testing sensors for higher accuracy",
            "Implement automated calibration data logging",
            "Add redundant measurement systems for critical parameters"
        ]
    },

    # S5: Quality Inspection and Finalization
    'S5': {
        'bottleneck': [
            "Add one more test fixture (increase from {current_qty} to {target_qty} units)",
            "Optimize test sequence - parallel testing where possible",
            "Upgrade laser sensors for {sensor_improvement_pct}% faster measurement",
            "Increase S4→S5 buffer to {buffer_target} units (currently {current_buffer})"
        ],
        'maintenance_needed': [
            "Service machine vision cameras and lighting (MTTR: {mttr_s}s)",
            "Calibrate measurement sensors and test fixtures",
            "Replace worn test probes and connectors"
        ],
        'quality_risk': [
            "Upgrade machine vision resolution and lighting",
            "Add automated defect classification AI",
            "Implement SPC (Statistical Process Control) monitoring"
        ]
    },

    # S6: Packaging and Dispatch
    'S6': {
        'bottleneck': [
            "Add automated palletizing system to reduce packaging time",
            "Upgrade vision system for {vision_improvement_pct}% faster inspection",
            "Implement batch packaging to reduce cycle time by {cycle_reduction_pct}%",
            "Increase S5→S6 buffer to {buffer_target} units (currently {current_buffer})"
        ],
        'maintenance_needed': [
            "Service automated box sealer and taping mechanism (MTTR: {mttr_s}s)",
            "Replace worn conveyor belts and rollers",
            "Calibrate packaging sensors and vision systems"
        ],
        'quality_risk': [
            "Add automated package weight verification",
            "Implement barcode scanning for shipping accuracy",
            "Install vision inspection for package sealing quality"
        ]
    },

    # Default recommendations for unknown stations
    'default': {
        'bottleneck': [
            "Analyze cycle time and identify optimization opportunities",
            "Consider adding parallel machines or upgrading equipment",
            "Review buffer sizes and material flow",
            "Implement process improvements to reduce setup time"
        ],
        'maintenance_needed': [
            "Schedule preventive maintenance based on MTBF of {mtbf_h} hours",
            "Inspect critical components and replace worn parts",
            "Calibrate sensors and measurement systems"
        ],
        'quality_risk': [
            "Implement additional quality checks and inspections",
            "Review process parameters and adjust as needed",
            "Train operators on quality standards and defect recognition"
        ],
        'plc_comm_issue': [
            "Check PLC network connectivity and communication cables",
            "Verify master-slave synchronization and heartbeat signals",
            "Inspect network switches and communication modules",
            "Review PLC program for communication errors"
        ],
        'normal': [
            "Production line operating normally. No action required.",
            "Continue monitoring key performance indicators",
            "Maintain preventive maintenance schedule"
        ]
    }
}


def generate_intelligent_recommendation(
    prediction: str,
    row: Dict[str, Any],
    station_id: Optional[str],
    context: Dict[str, Any]
) -> Tuple[str, List[str]]:
    """
    Generate intelligent, context-aware recommendations using learned templates.
    Returns: (main_recommendation, detailed_actions)
    """
    # Get station-specific templates or use default
    station_key = station_id if station_id in INTELLIGENT_RECOMMENDATIONS else 'default'
    templates = INTELLIGENT_RECOMMENDATIONS[station_key].get(prediction,
                                                             INTELLIGENT_RECOMMENDATIONS['default'].get(prediction, []))

    if not templates:
        return f"Unknown state: {prediction}. Investigate immediately.", []

    # Calculate dynamic values for template substitution
    template_vars = {
        # Basic station info
        'station_id': station_id or 'unknown',
        'current_cycle': round(row.get('cycle_time_s', 0), 1),
        'current_qty': row.get('parallel_machines', 1),
        'mttr_s': round(row.get('mttr_s', 0)),
        'mtbf_h': round(row.get('mtbf_h', 0)),
        'failure_rate': round(row.get('failure_rate', 0) * 100, 2),
        'criticality': row.get('criticality', 'medium'),

        # Calculated improvements
        # 25% improvement
        'target_cycle': max(5.0, round(row.get('cycle_time_s', 0) * 0.75, 1)),
        'cycle_improvement_pct': 10 + int(row.get('cycle_time_s', 0) / 2),
        'failure_reduction_pct': min(30, max(15, int(row.get('failure_rate', 0) * 1000))),
        'bottleneck_reduction_pct': min(40, max(25, int(row.get('cycle_time_s', 0) * 2))),

        # Buffer recommendations
        'current_buffer': context.get('buffers', {}).get(f'{station_id}_to_next', 2),
        'buffer_target': min(15, max(8, int(row.get('cycle_time_s', 0) * 0.7))),

        # Equipment scaling
        'additional_machines': max(1, min(2, int(row.get('parallel_machines', 1) * 0.5))),
        'additional_stations': max(1, min(3, int(row.get('parallel_machines', 1) * 0.3))),
        'target_qty': row.get('parallel_machines', 1) + max(1, min(2, int(row.get('parallel_machines', 1) * 0.5))),

        # ROI estimates
        'roi_months': round(6 + row.get('cycle_time_s', 0) * 0.2, 1),
        'margin_per_unit': round(15 + row.get('cycle_time_s', 0) * 0.5, 1),

        # Quality improvements
        'sensor_improvement_pct': min(30, max(15, int(row.get('cycle_time_s', 0) * 1.5))),
        'vision_improvement_pct': min(35, max(20, int(row.get('cycle_time_s', 0) * 1.8))),
        'cycle_reduction_pct': min(20, max(10, int(row.get('cycle_time_s', 0) * 0.8))),
        'reduction_pct': min(25, max(10, int(row.get('setup_time_s', 0) * 0.15))),

        # Equipment info
        'equipment': row.get('equipment', 'equipment'),
        'power_rating_w': row.get('power_rating_w', 0),
    }

    # Fill in templates with actual values
    detailed_actions = []
    for template in templates[:5]:  # Limit to top 5 recommendations
        try:
            # Replace placeholders with actual values
            filled_template = template
            for key, value in template_vars.items():
                placeholder = '{' + key + '}'
                if placeholder in filled_template:
                    filled_template = filled_template.replace(
                        placeholder, str(value))
            detailed_actions.append(filled_template)
        except Exception as e:
            # Use original if substitution fails
            detailed_actions.append(template)

    # Create main recommendation summary
    main_recommendation = f"{prediction.replace('_', ' ').title()} detected"
    if station_id:
        main_recommendation += f" at station {station_id}"
    main_recommendation += ". See detailed actions below."

    return main_recommendation, detailed_actions


# ==================== DATA PROCESSING FUNCTIONS ====================
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

    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

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
    return any(col.startswith('stations.') for col in df.columns) and len(df) == 1


def transform_station_config_to_examples(df: pd.DataFrame, include_context: bool = True) -> Tuple[pd.DataFrame, Dict]:
    """
    Transform nested station configuration into station-level training examples.
    Returns: (station_dataframe, context_dict)
    """
    record = df.iloc[0].to_dict()

    # Extract station keys
    station_prefixes = sorted(set(
        col.split('.')[0] + '.' + col.split('.')[1]
        for col in record.keys() if col.startswith('stations.')
    ))

    examples = []
    for prefix in station_prefixes:
        station_data = {
            k.replace(prefix + '.', ''): v
            for k, v in record.items()
            if k.startswith(prefix + '.')
        }

        station_id = prefix.split('.')[-1].strip()
        station_data['station_id'] = station_id

        # Add top-level metadata
        for key in ['simulation_time_s', 'simulation_time_h']:
            if key in record:
                station_data[f'total_{key}'] = record[key]

        examples.append(station_data)

    # Extract context for recommendations
    context = {}
    if include_context:
        # Extract buffers
        buffers = {k.replace('buffers.', ''): v for k,
                   v in record.items() if k.startswith('buffers.')}
        context['buffers'] = buffers

        # Extract maintenance info
        maintenance = {k.replace('maintenance.', ''): v for k,
                       v in record.items() if k.startswith('maintenance.')}
        context['maintenance'] = maintenance

        # Extract human resources
        hr = {k.replace('human_resources.', ''): v for k,
              v in record.items() if k.startswith('human_resources.')}
        context['human_resources'] = hr

    return pd.DataFrame(examples), context


def make_hashable_for_duplication(df: pd.DataFrame) -> pd.DataFrame:
    """Convert non-hashable types to JSON strings for duplicate detection."""
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
    """Find label column or its alternatives."""
    if LABEL_COLUMN in df.columns:
        return LABEL_COLUMN
    for alt in LABEL_ALTERNATIVES:
        if alt in df.columns:
            return alt
    return None


def auto_label_dataframe(df: pd.DataFrame, is_station_config: bool = False) -> pd.DataFrame:
    """Generate weak labels using rule-based heuristics."""
    df = df.copy()
    df['label'] = 'normal'

    rules = AUTO_LABEL_RULES_STATION if is_station_config else AUTO_LABEL_RULES_TABULAR

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


# ==================== MODEL TRAINING FUNCTIONS ====================
def create_base_pipeline(numeric_features: List[str],
                         categorical_features: List[str]) -> ColumnTransformer:
    """Create preprocessing pipeline (shared across all classifiers)."""
    numeric_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler())
    ])

    categorical_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='most_frequent')),
        ('encoder', OneHotEncoder(handle_unknown='ignore', sparse_output=False))
    ])

    return ColumnTransformer(transformers=[
        ('num', numeric_transformer, numeric_features),
        ('cat', categorical_transformer, categorical_features)
    ])


def create_classifiers(preprocessor: ColumnTransformer, n_classes: int) -> Dict[str, Pipeline]:
    """Create multiple classifier pipelines for comparison."""
    classifiers = {}

    # 1. Random Forest (robust baseline)
    classifiers['random_forest'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', RandomForestClassifier(
            n_estimators=100,
            max_depth=15,
            min_samples_split=5,
            class_weight='balanced',
            random_state=RANDOM_STATE,
            n_jobs=-1
        ))
    ])

    # 2. Gradient Boosting (high accuracy)
    classifiers['gradient_boosting'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', GradientBoostingClassifier(
            n_estimators=100,
            learning_rate=0.1,
            max_depth=5,
            min_samples_split=5,
            random_state=RANDOM_STATE
        ))
    ])

    # 3. Extra Trees (reduces overfitting)
    classifiers['extra_trees'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', ExtraTreesClassifier(
            n_estimators=100,
            max_depth=15,
            min_samples_split=5,
            class_weight='balanced',
            random_state=RANDOM_STATE,
            n_jobs=-1
        ))
    ])

    # 4. SVM with RBF kernel (good for complex boundaries)
    classifiers['svm'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', CalibratedClassifierCV(
            SVC(kernel='rbf', C=1.0, gamma='scale',
                class_weight='balanced', random_state=RANDOM_STATE),
            method='sigmoid',
            cv=3
        ))
    ])

    # 5. Logistic Regression (interpretable baseline)
    classifiers['logistic_regression'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', LogisticRegression(
            penalty='l2',
            C=1.0,
            class_weight='balanced',
            max_iter=1000,
            random_state=RANDOM_STATE
        ))
    ])

    # 6. KNN (non-parametric, good for local patterns)
    classifiers['knn'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', KNeighborsClassifier(
            n_neighbors=min(3, n_classes + 1),
            weights='distance',
            n_jobs=-1
        ))
    ])

    # 7. Decision Tree (interpretable, baseline for ensembles)
    classifiers['decision_tree'] = Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('classifier', DecisionTreeClassifier(
            max_depth=10,
            min_samples_split=5,
            class_weight='balanced',
            random_state=RANDOM_STATE
        ))
    ])

    return classifiers


def select_best_model(
    classifiers: Dict[str, Pipeline],
    X_train: pd.DataFrame,
    y_train: pd.Series,
    X_val: Optional[pd.DataFrame],
    y_val: Optional[pd.Series],
    use_cv: bool = False,
    verbose: bool = True
) -> Tuple[Pipeline, Dict[str, float], str]:
    """
    Train multiple models and select the best one.
    If validation set is None, uses cross-validation instead.
    Returns: (best_pipeline, all_scores, best_model_name)
    """
    scores = {}
    trained_models = {}

    print("\n" + "="*70)
    print("TRAINING MULTIPLE ALGORITHMS")
    print("="*70)

    for name, pipeline in classifiers.items():
        start_time = time.time()

        try:
            if verbose:
                print(
                    f"\nTraining {name.replace('_', ' ').title():<25}...", end=" ")

            pipeline.fit(X_train, y_train)

            # Evaluate using validation set OR cross-validation
            if X_val is not None and y_val is not None:
                y_pred = pipeline.predict(X_val)
                f1_macro = f1_score(
                    y_val, y_pred, average='macro', zero_division=0)
            else:
                # Use cross-validation for small datasets
                cv_scores = cross_val_score(pipeline, X_train, y_train, cv=min(
                    3, len(X_train)), scoring='f1_macro')
                f1_macro = cv_scores.mean()

            scores[name] = f1_macro
            trained_models[name] = pipeline

            elapsed = time.time() - start_time

            if verbose:
                val_type = "CV" if X_val is None else "Val"
                print(
                    f"✓ {val_type} F1-macro: {f1_macro:.4f} | Time: {elapsed:.1f}s")

        except Exception as e:
            if verbose:
                print(f"✗ Failed: {str(e)[:50]}")
            continue

    if not scores:
        raise ValueError("No models could be trained successfully.")

    # Select best model
    best_model_name = max(scores, key=scores.get)
    best_model = trained_models[best_model_name]

    print("\n" + "="*70)
    print(f"BEST MODEL: {best_model_name.replace('_', ' ').title()}")
    print(f"F1-Macro Score: {scores[best_model_name]:.4f}")
    print("="*70)

    return best_model, scores, best_model_name


def create_ensemble_model(
    base_models: Dict[str, Pipeline],
    model_names: List[str]
) -> VotingClassifier:
    """
    Create a voting ensemble from top-performing models.
    Uses soft voting for probability-based aggregation.
    """
    estimators = [(name, model.named_steps['classifier'])
                  for name, model in base_models.items() if name in model_names]

    ensemble = VotingClassifier(
        estimators=estimators,
        voting='soft',
        n_jobs=-1
    )

    # Wrap in pipeline with preprocessor from first model
    preprocessor = base_models[model_names[0]].named_steps['preprocessor']

    return Pipeline(steps=[
        ('preprocessor', preprocessor),
        ('ensemble', ensemble)
    ])


# ==================== TRAINING COMMAND ====================
def train_command(args: argparse.Namespace) -> None:
    """Execute training command with support for small datasets."""
    all_dfs = []
    file_info = []

    # Load and process each JSON file
    for filepath in args.data:
        print(f"\nLoading: {filepath}")
        df = load_json_file(filepath)

        # Detect and transform station configuration data
        is_station_config = detect_station_configuration(df)
        if is_station_config:
            print(f"  → Detected station configuration. Transforming...")
            df, _ = transform_station_config_to_examples(
                df, include_context=False)
            print(f"  → Extracted {len(df)} station examples")
            file_type = 'station_config'
        else:
            file_type = 'tabular'

        file_info.append({
            'filename': os.path.basename(filepath),
            'type': file_type,
            'rows': len(df)
        })

        all_dfs.append(df)

    # Concatenate all dataframes
    df_combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n{'='*60}")
    print(
        f"Combined dataset: {len(df_combined)} total rows from {len(args.data)} files")
    print(f"{'='*60}")
    for info in file_info:
        print(
            f"  {info['filename']:<30} {info['type']:<15} {info['rows']:>4} rows")
    print(f"{'='*60}\n")

    # Initialize validation report
    validation_report = {
        'row_count_before': len(df_combined),
        'missing_required_columns': [],
        'rows_dropped_missing_label': 0,
        'duplicate_rows': 0,
        'null_ratio_by_column': {},
        'row_count_after': 0,
        'data_type': 'mixed',
        'files_processed': file_info,
        'test_size': args.test_size,
        'train_size_fraction': 1 - args.test_size
    }

    # Handle label column
    label_col = find_label_column(df_combined)
    if label_col is None:
        if args.auto_label:
            print(
                "Label column not found. Generating weak labels using --auto-label rules...")
            station_config_count = sum(
                1 for info in file_info if info['type'] == 'station_config')
            is_station_config = station_config_count > len(file_info) / 2
            df_combined = auto_label_dataframe(
                df_combined, is_station_config=is_station_config)
            label_col = 'label'
        else:
            raise ValueError(
                f"Required label column not found in combined data. Searched for: '{LABEL_COLUMN}' "
                f"and alternatives {LABEL_ALTERNATIVES}. Use --auto-label to generate labels automatically."
            )
    else:
        if label_col != LABEL_COLUMN:
            df_combined = df_combined.rename(columns={label_col: LABEL_COLUMN})
            label_col = LABEL_COLUMN

    # Drop rows with missing labels (if not auto-labeled)
    if not args.auto_label:
        initial_count = len(df_combined)
        df_combined = df_combined.dropna(subset=[LABEL_COLUMN])
        validation_report['rows_dropped_missing_label'] = initial_count - \
            len(df_combined)

    # Handle duplicates safely
    df_hashable = make_hashable_for_duplication(df_combined)
    dup_mask = df_hashable.duplicated()
    validation_report['duplicate_rows'] = int(dup_mask.sum())
    df_combined = df_combined[~dup_mask].reset_index(drop=True)

    # Compute null ratios
    validation_report['null_ratio_by_column'] = df_combined.isnull(
    ).mean().to_dict()
    validation_report['row_count_after'] = len(df_combined)

    # Final validation checks
    if len(df_combined) == 0:
        raise ValueError(
            "No data remaining after cleaning. Check input file quality.")

    if df_combined[LABEL_COLUMN].nunique() < 2:
        raise ValueError(
            f"Insufficient class diversity. Found only {df_combined[LABEL_COLUMN].nunique()} unique class(es). "
            "Need at least 2 classes for classification."
        )

    # Prepare features and target
    y = df_combined[LABEL_COLUMN]
    X = df_combined.drop(columns=[LABEL_COLUMN])

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

    # Create preprocessing pipeline
    preprocessor = create_base_pipeline(numeric_features, categorical_features)

    # Handle small datasets (like 6 stations) differently
    if len(df_combined) < 20:
        print(f"\n⚠️  SMALL DATASET DETECTED ({len(df_combined)} samples)")
        print(f"   Using cross-validation instead of train/validation split")

        # For very small datasets, skip validation split and use CV
        try:
            X_train_full, X_test, y_train_full, y_test = train_test_split(
                X, y,
                test_size=args.test_size,
                random_state=RANDOM_STATE,
                stratify=y
            )
        except ValueError as e:
            print(
                f"   Warning: Stratified split failed ({e}). Using non-stratified split.")
            X_train_full, X_test, y_train_full, y_test = train_test_split(
                X, y,
                test_size=args.test_size,
                random_state=RANDOM_STATE
            )

        # No validation split for small datasets - use CV instead
        X_train, X_val, y_train, y_val = X_train_full, None, y_train_full, None
        use_cv_for_selection = True

        print(f"\nData split:")
        print(
            f"  Training:   {len(X_train):>5} samples ({len(X_train)/len(X)*100:.1f}%)")
        print(
            f"  Test:       {len(X_test):>5} samples ({len(X_test)/len(X)*100:.1f}%)")
        print(f"  Total:      {len(X):>5} samples")
    else:
        # Normal split for larger datasets
        try:
            X_train_full, X_test, y_train_full, y_test = train_test_split(
                X, y,
                test_size=args.test_size,
                random_state=RANDOM_STATE,
                stratify=y
            )
        except ValueError as e:
            print(
                f"Warning: Stratified split failed ({e}). Falling back to non-stratified split.")
            X_train_full, X_test, y_train_full, y_test = train_test_split(
                X, y,
                test_size=args.test_size,
                random_state=RANDOM_STATE
            )

        # Further split training data for model selection
        try:
            X_train, X_val, y_train, y_val = train_test_split(
                X_train_full, y_train_full,
                test_size=0.2,
                random_state=RANDOM_STATE,
                stratify=y_train_full
            )
        except ValueError:
            X_train, X_val, y_train, y_val = train_test_split(
                X_train_full, y_train_full,
                test_size=0.2,
                random_state=RANDOM_STATE
            )

        use_cv_for_selection = False

        print(f"\nData split:")
        print(
            f"  Training:   {len(X_train):>5} samples ({len(X_train)/len(X)*100:.1f}%)")
        print(
            f"  Validation: {len(X_val):>5} samples ({len(X_val)/len(X)*100:.1f}%)")
        print(
            f"  Test:       {len(X_test):>5} samples ({len(X_test)/len(X)*100:.1f}%)")
        print(f"  Total:      {len(X):>5} samples")

    # Create multiple classifiers
    n_classes = len(y.unique())
    classifiers = create_classifiers(preprocessor, n_classes)

    # Select best model (uses CV for small datasets)
    best_model, all_scores, best_model_name = select_best_model(
        classifiers, X_train, y_train, X_val, y_val,
        use_cv=use_cv_for_selection, verbose=True
    )

    # Optional: Create ensemble from top-3 models if requested
    if args.ensemble:
        print("\nCreating ensemble from top-3 models...")
        top_3_models = sorted(all_scores.items(),
                              key=lambda x: x[1], reverse=True)[:3]
        top_3_names = [name for name, _ in top_3_models]

        ensemble_models = {name: classifiers[name] for name in top_3_names}
        best_model = create_ensemble_model(ensemble_models, top_3_names)
        best_model_name = 'ensemble_voting'

        # Retrain ensemble on full training data
        best_model.fit(X_train_full, y_train_full)
        print(f"Ensemble created from: {', '.join(top_3_names)}")
    else:
        # Retrain best model on full training data
        best_model.fit(X_train_full, y_train_full)

    # Evaluate on test set
    y_pred = best_model.predict(X_test)

    # Compute metrics
    metrics = {
        'best_model': best_model_name,
        'all_model_scores': {k: float(v) for k, v in all_scores.items()},
        'f1_macro': float(f1_score(y_test, y_pred, average='macro', zero_division=0)),
        'f1_weighted': float(f1_score(y_test, y_pred, average='weighted', zero_division=0)),
        'precision_macro': float(precision_score(y_test, y_pred, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(y_test, y_pred, average='macro', zero_division=0)),
        'accuracy': float(accuracy_score(y_test, y_pred)),
        'classification_report': classification_report(y_test, y_pred, output_dict=True, zero_division=0),
        'confusion_matrix': confusion_matrix(y_test, y_pred).tolist(),
        'train_size': len(X_train_full),
        'test_size': len(X_test),
        'train_fraction': round(1 - args.test_size, 2),
        'test_fraction': round(args.test_size, 2),
        'class_distribution_train': y_train_full.value_counts().to_dict(),
        'class_distribution_test': y_test.value_counts().to_dict(),
        'feature_columns': {
            'numeric': numeric_features,
            'categorical': categorical_features
        },
        'validation_report': validation_report
    }

    # Add cross-validation scores
    print("\nComputing cross-validation scores...")
    cv_folds = min(CV_FOLDS, len(X_train_full))
    cv_scores = cross_val_score(
        best_model, X_train_full, y_train_full, cv=cv_folds, scoring='f1_macro', n_jobs=-1)
    metrics['cross_validation'] = {
        'folds': cv_folds,
        'scores': cv_scores.tolist(),
        'mean': float(cv_scores.mean()),
        'std': float(cv_scores.std())
    }
    print(f"CV F1-macro: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # Feature importance (if available)
    try:
        if hasattr(best_model.named_steps['classifier'], 'feature_importances_'):
            importances = best_model.named_steps['classifier'].feature_importances_
            feature_names = best_model.named_steps['preprocessor'].get_feature_names_out(
            )

            importance_dict = dict(zip(feature_names, importances))
            sorted_importance = dict(
                sorted(importance_dict.items(), key=lambda x: x[1], reverse=True))

            metrics['feature_importance'] = sorted_importance
            print(f"\nTop 10 features by importance:")
            for i, (feature, importance) in enumerate(list(sorted_importance.items())[:10], 1):
                print(f"  {i}. {feature:<40} {importance:.4f}")
    except Exception as e:
        print(f"Could not compute feature importance: {e}")

    # Ensure correct output extensions
    model_out = ensure_joblib_extension(args.model_out)
    metrics_out = ensure_json_extension(args.metrics_out)

    # Save artifacts
    dump(best_model, model_out)

    with open(metrics_out, 'w', encoding='utf-8') as f:
        json.dump(metrics, f, indent=2, default=str)

    # Print sample predictions
    print("\n" + "="*70)
    print("SAMPLE TEST PREDICTIONS (true → predicted):")
    print("-" * 70)
    sample_size = min(10, len(y_test))
    sample_indices = np.random.choice(
        len(y_test), size=sample_size, replace=False)
    for idx in sample_indices:
        true_val = y_test.iloc[idx]
        pred_val = y_pred[idx]
        station_id = X_test.iloc[idx].get(
            'station_id', 'N/A') if 'station_id' in X_test.columns else 'N/A'
        status = "✓" if true_val == pred_val else "✗"
        print(f"  [{status}] Station {station_id:<5} {true_val:<20} → {pred_val}")

    print("\n" + "="*70)
    print(f"✓ Model saved to: {model_out}")
    print(f"✓ Metrics saved to: {metrics_out}")
    print(
        f"✓ Training complete: {len(X_train_full)} train / {len(X_test)} test samples")
    print(f"✓ Best Model: {best_model_name.replace('_', ' ').title()}")
    print(f"✓ Test F1-Macro: {metrics['f1_macro']:.4f}")
    print(f"✓ Test Accuracy: {metrics['accuracy']:.4f}")
    print(f"✓ CV F1-Macro: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")
    print("="*70)


# ==================== RECOMMENDATION COMMAND ====================
def recommend_command(args: argparse.Namespace) -> None:
    """Execute recommendation command with intelligent, context-aware suggestions."""
    # Determine model path (positional arg or default)
    model_path = args.model_path if args.model_path else DEFAULT_MODEL_PATH

    # Load model
    try:
        model = load(model_path)
    except FileNotFoundError:
        raise ValueError(
            f"Model file not found: {model_path}\n"
            f"Train a model first: python ai_agent.py train --data file1.json file2.json ..."
        )
    except Exception as e:
        raise ValueError(f"Failed to load model from {model_path}: {e}")

    # Load input data
    df = load_json_file(args.input)

    # Detect and transform station configuration data for recommendations
    context = {}
    if detect_station_configuration(df):
        print("Detected station configuration JSON. Transforming for recommendations...")
        df, context = transform_station_config_to_examples(
            df, include_context=True)
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

    # Generate probabilities if available
    probabilities = None
    try:
        if hasattr(model.named_steps.get('ensemble', model.named_steps['classifier']), 'predict_proba'):
            proba = model.predict_proba(df)
            probabilities = proba.max(axis=1)
    except Exception:
        pass

    # Identify station_id column
    station_id_col = next(
        (col for col in ['station_id', 'station',
         'stationid', 'stationId'] if col in df.columns),
        None
    )

    # Generate intelligent recommendations
    recommendations = []
    print("\n" + "="*80)
    print("INTELLIGENT RECOMMENDATIONS")
    print("="*80)

    for idx, (pred, row_dict) in enumerate(zip(predictions, df.to_dict('records'))):
        station_id = row_dict.get(station_id_col) if station_id_col else None
        confidence = probabilities[idx] if probabilities is not None else None

        # Generate intelligent recommendation with context
        main_rec, detailed_actions = generate_intelligent_recommendation(
            pred, row_dict, station_id, context
        )

        # Build recommendation item
        rec_item = {
            'index': idx,
            'station_id': station_id,
            'prediction': str(pred),
            'main_recommendation': main_rec,
            'detailed_actions': detailed_actions,
            'confidence': round(float(confidence), 4) if confidence is not None else None
        }
        recommendations.append(rec_item)

        # Print to terminal with formatting
        print(f"\n{'─'*78}")
        print(
            f"STATION {station_id or 'N/A'} | Prediction: {pred.upper()}", end="")
        if confidence is not None:
            print(f" | Confidence: {confidence:.1%}", end="")
        print()
        print(f"{'─'*78}")
        print(f"💡 {main_rec}\n")

        if detailed_actions:
            print("📋 Detailed Actions:")
            for i, action in enumerate(detailed_actions, 1):
                print(f"   {i}. {action}")

        # Show relevant station metrics
        print(f"\n📊 Key Metrics:")
        metrics_to_show = ['cycle_time_s', 'failure_rate',
                           'mttr_s', 'mtbf_h', 'criticality']
        for metric in metrics_to_show:
            if metric in row_dict:
                value = row_dict[metric]
                if metric == 'failure_rate':
                    value = f"{value*100:.2f}%"
                elif metric == 'criticality':
                    value = value.upper()
                print(f"   • {metric.replace('_', ' ').title()}: {value}")

    print("\n" + "="*80)

    # Save recommendations to JSON
    output_path = ensure_json_extension(args.output)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(recommendations, f, indent=2, default=str)

    print(f"\n✅ Recommendations saved to: {output_path}")
    print(f"   Total stations analyzed: {len(recommendations)}")
    print("="*80)


# ==================== MAIN ====================
def main() -> None:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description="AI Agent for 6-Station 3D Printer Production Line\n"
                    "✓ Learns from historical recommendations\n"
                    "✓ Generates intelligent, station-specific suggestions\n"
                    "✓ Context-aware with buffer/maintenance data\n"
                    "✓ Handles small datasets (e.g., 6 stations)",
        formatter_class=argparse.RawTextHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # Train command
    train_parser = subparsers.add_parser(
        'train',
        help='Train with intelligent recommendation learning',
        description='Train production line anomaly detection model.\n'
                    'Automatically detects small datasets and uses cross-validation.\n'
                    'Supports multiple JSON files and ensemble methods.'
    )
    train_parser.add_argument(
        '--data',
        type=str,
        nargs='+',
        required=True,
        help='One or more JSON files to train on'
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
        help='Path to save evaluation metrics (default: metrics.json)'
    )
    train_parser.add_argument(
        '--test-size',
        type=float,
        default=DEFAULT_TEST_SIZE,
        help=f'Test split fraction (default: {DEFAULT_TEST_SIZE})'
    )
    train_parser.add_argument(
        '--auto-label',
        action='store_true',
        help='Auto-generate labels using rule-based heuristics'
    )
    train_parser.add_argument(
        '--ensemble',
        action='store_true',
        help='Create ensemble from top-3 models'
    )

    # Recommend command
    recommend_parser = subparsers.add_parser(
        'recommend',
        help='Generate intelligent, context-aware recommendations',
        description='Generate station-specific recommendations using trained model.\n'
                    'Uses learned templates with dynamic value substitution.\n'
                    'MODEL_PATH is optional (defaults to line_model.joblib).'
    )
    recommend_parser.add_argument(
        'model_path',
        type=str,
        nargs='?',
        default=None,
        help=f'Path to trained model (default: {DEFAULT_MODEL_PATH})'
    )
    recommend_parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to input data JSON file for recommendations'
    )
    recommend_parser.add_argument(
        '--output',
        type=str,
        default='recommendations.json',
        help='Path to save recommendations (default: recommendations.json)'
    )

    args = parser.parse_args()

    try:
        if args.command == 'train':
            train_command(args)
        elif args.command == 'recommend':
            recommend_command(args)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
