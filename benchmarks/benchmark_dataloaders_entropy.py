#!/usr/bin/env python3
"""
Entropy Measurement Script for SLAF DataLoaders

This script measures the entropy (randomness) of different SLAF loading strategies
by analyzing cell ID distributions across training batches.
"""

import random
import time
from dataclasses import dataclass
from itertools import combinations

import numpy as np
from tqdm import tqdm

from slaf.core.slaf import SLAFArray
from slaf.ml.dataloaders import SLAFDataLoader


@dataclass
class EntropyMetrics:
    """Container for entropy measurement results."""

    strategy: str
    # Within-batch entropy (how random are the cells within each batch)
    within_batch_l1: float
    # Across-batch entropy (how much batch composition changes between batches)
    across_batch_l1: float  # L1 distance between consecutive batch centroids
    # Summary stats
    n_batches: int
    total_cells: int
    measurement_time: float


class EntropyCalculator:
    """Calculate various entropy metrics for cell ID distributions."""

    def __init__(self, max_num_cells: int):
        self.max_num_cells = max_num_cells

        # Pre-compute baseline metrics for normalization
        self._compute_baselines()

    def _compute_baselines(self):
        """Compute baseline metrics for sequential and random distributions."""
        batch_size = 32

        # Sequential baseline: consecutive IDs
        sequential_ids = list(range(batch_size))
        self.sequential_l1 = self._compute_l1_distance(sequential_ids)

        # Random baseline: uniformly random from full space
        random_ids = np.random.choice(
            self.max_num_cells, size=batch_size, replace=False
        )
        self.random_l1 = self._compute_l1_distance(random_ids)

        # For across-batch metrics, we need to simulate multiple batches
        # Sequential: batches of consecutive IDs
        sequential_batches = [
            list(range(i * batch_size, (i + 1) * batch_size)) for i in range(10)
        ]
        self.sequential_across_l1 = self._compute_across_batch_l1(sequential_batches)

        # Random: batches of random IDs
        random_batches = [
            np.random.choice(
                self.max_num_cells, size=batch_size, replace=False
            ).tolist()
            for _ in range(10)
        ]
        self.random_across_l1 = self._compute_across_batch_l1(random_batches)

    def _compute_l1_distance(self, batch_ids: list[int]) -> float:
        """Compute L1 distance between sampled pairs of cell IDs within a batch."""
        if len(batch_ids) < 2:
            return 0.0

        # Calculate available pairs
        available_pairs = len(batch_ids) * (len(batch_ids) - 1) // 2
        if available_pairs == 0:
            return 0.0

        # Sample fewer pairs for speed, but don't exceed available pairs
        n_pairs = min(100, available_pairs)

        # Sample pairs and compute L1 distances using simple list comprehension
        pairs = random.sample(list(combinations(batch_ids, 2)), n_pairs)
        if pairs:
            # Simple L1 distance calculation
            distances = [abs(ai - aj) for ai, aj in pairs]
            return float(np.mean(distances))
        return 0.0

    def _compute_across_batch_l1(self, all_batch_ids: list[list[int]]) -> float:
        """Compute L1 distance between pairs of cells from consecutive batches."""
        if len(all_batch_ids) < 2:
            return 0.0

        distances = []
        # Use consecutive batch pairs for better performance and meaningful measurement
        for i in range(len(all_batch_ids) - 1):
            batch1 = np.array(all_batch_ids[i])
            batch2 = np.array(all_batch_ids[i + 1])

            # Handle edge cases where batches have very few cells
            if len(batch1) < 1 or len(batch2) < 1:
                continue

            # Sample fewer cell pairs between consecutive batches for speed
            # Use the minimum of the two batch sizes to avoid shape mismatches
            max_possible_pairs = min(len(batch1), len(batch2))
            n_cell_pairs = min(100, max_possible_pairs)

            if n_cell_pairs == 0:
                continue

            try:
                # Sample indices directly for efficiency, ensuring we don't exceed batch sizes
                indices1 = np.random.choice(
                    len(batch1), size=n_cell_pairs, replace=False
                )
                indices2 = np.random.choice(
                    len(batch2), size=n_cell_pairs, replace=False
                )

                # Compute L1 distances between sampled cells
                batch_distances = np.abs(batch1[indices1] - batch2[indices2])
                distances.extend(batch_distances.tolist())

            except (ValueError, IndexError):
                # Skip this batch pair if there's any issue
                continue

        if distances:
            return float(np.mean(distances))
        return 0.0

    def compute_within_batch_entropy(
        self, batch_ids: list[list[int]]
    ) -> dict[str, float]:
        """Compute within-batch entropy metrics for a list of batches."""

        # Handle both single batch and list of batches
        if not batch_ids:
            return {"l1_distance": 0.0}

        # If batch_ids is a single batch (list of ints), wrap it in a list
        if isinstance(batch_ids[0], int):
            batch_ids = [batch_ids]

        # Compute metrics for each batch
        l1_distances = []

        for batch in batch_ids:
            if len(batch) < 2:
                continue

            l1_distance = self._compute_l1_distance(batch)
            l1_distances.append(l1_distance)

        if l1_distances:
            return {"l1_distance": float(np.mean(l1_distances))}
        return {"l1_distance": 0.0}

    def compute_across_batch_entropy(
        self, all_batch_ids: list[list[int]]
    ) -> dict[str, float]:
        """Compute across-batch entropy metrics."""

        # Compute L1 distance between consecutive batches
        l1_distance = self._compute_across_batch_l1(all_batch_ids)

        return {
            "l1_distance": l1_distance,
        }

    def normalize_score(
        self, metric_value: float, sequential_val: float, random_val: float
    ) -> float:
        """Normalize metric to [0,1] scale where 0=sequential, 1=random."""
        if abs(random_val - sequential_val) < 1e-10:
            return 0.5  # Avoid division by zero

        score = (metric_value - sequential_val) / (random_val - sequential_val)
        return score  # Don't clip - show when metrics exceed random baseline


class EntropyBenchmark:
    """Benchmark entropy for different SLAF loading strategies."""

    def __init__(self, slaf_array: SLAFArray):
        self.slaf_array = slaf_array
        print("Dataset loaded successfully")
        # Get max number of cells from metadata
        self.max_num_cells = self.slaf_array.cells.count_rows()
        print(f"Dataset has {self.max_num_cells:,} cells")

        # Initialize entropy calculator
        self.entropy_calc = EntropyCalculator(self.max_num_cells)

        # Define strategies to test - match benchmark_dataloaders_internal.py exactly
        self.strategies = [
            (
                "sequential",
                False,  # by_fragment
                False,  # use_mos
                "Sequential loading (lowest entropy)",
                50,  # batches_per_chunk
            ),
            (
                "fragment",
                True,  # by_fragment
                False,  # use_mos
                "Fragment-based loading (higher entropy)",
                50,  # batches_per_chunk
            ),
            (
                "mos",
                False,  # by_fragment
                True,  # use_mos
                "Mixture of Scanners (maximum entropy)",
                1,  # batches_per_chunk
            ),
        ]

    def run_entropy_measurement(
        self, strategy_name: str, by_fragment: bool, use_mos: bool, n_batches: int
    ) -> EntropyMetrics:
        """Run entropy measurement for a specific strategy."""
        print(f"\n🔍 Measuring entropy for {strategy_name} strategy...")

        # Create dataloader with specified strategy - match benchmark_dataloaders_internal.py exactly
        dataloader = SLAFDataLoader(
            slaf_array=self.slaf_array,
            batch_size=32,
            n_epochs=1000,
            raw_mode=True,
            verbose=False,
            by_fragment=by_fragment,
            use_mixture_of_scanners=use_mos,
            n_scanners=16,
            prefetch_batch_size=4194304 if use_mos else 8192,  # 4M rows for MoS
            batches_per_chunk=(
                50 if not use_mos else 1
            ),  # 50 for sequential/fragment, 1 for MoS
        )

        # Collect cell IDs from batches
        all_batch_ids = []
        total_cells = 0
        start_time = time.time()

        print(f"  - Collecting {n_batches} batches...")
        for i, batch in enumerate(tqdm(dataloader, desc="Processing batches")):
            if i >= n_batches:
                break

            cell_ids = batch["cell_ids"]
            all_batch_ids.append(cell_ids)
            total_cells += len(cell_ids)

        measurement_time = time.time() - start_time

        print(
            f"  - Collected {len(all_batch_ids)} batches with {total_cells} total cells"
        )
        print(f"  - Measurement time: {measurement_time:.2f}s")

        # Compute entropy metrics
        print("  - Computing entropy metrics...")

        # Compute within-batch entropy for a sample of batches
        sample_batches = random.sample(all_batch_ids, min(100, len(all_batch_ids)))
        within_batch_metrics = {"l1_distance": []}

        for batch_ids in tqdm(sample_batches, desc="Computing within-batch entropy"):
            metrics = self.entropy_calc.compute_within_batch_entropy(batch_ids)
            for key in within_batch_metrics:
                within_batch_metrics[key].append(metrics[key])

        # Compute across-batch entropy
        across_batch_metrics = self.entropy_calc.compute_across_batch_entropy(
            all_batch_ids
        )

        print("  - Entropy computation complete!")

        return EntropyMetrics(
            strategy=strategy_name,
            within_batch_l1=np.mean(within_batch_metrics["l1_distance"]),
            across_batch_l1=across_batch_metrics["l1_distance"],
            n_batches=len(all_batch_ids),
            total_cells=total_cells,
            measurement_time=measurement_time,
        )

    def benchmark_all_strategies(self, n_batches: int = 100) -> list[EntropyMetrics]:
        """Run entropy measurement for all strategies."""
        results = []

        print(
            f"🚀 Testing {len(self.strategies)} entropy strategies with {n_batches:,} batches each..."
        )

        for (
            strategy_name,
            by_fragment,
            use_mos,
            _description,
            _batches_per_chunk,
        ) in tqdm(self.strategies, desc="Testing strategies", unit="strategy"):
            try:
                result = self.run_entropy_measurement(
                    strategy_name, by_fragment, use_mos, n_batches
                )
                results.append(result)
            except Exception as e:
                print(f"Error measuring {strategy_name}: {e}")
                continue

        # Generate random baseline directly
        print("\n🎲 Generating random baseline for comparison...")

        # Generate all random cell IDs at once (much faster than loop)
        total_cells_needed = n_batches * 32
        all_random_cell_ids = np.random.choice(
            self.max_num_cells, size=total_cells_needed, replace=False
        )

        # Chunk into batches of 32
        random_batch_ids = []
        for i in range(0, total_cells_needed, 32):
            batch_ids = all_random_cell_ids[i : i + 32].tolist()
            random_batch_ids.append(batch_ids)

        print(
            f"  - Generated {len(random_batch_ids)} random batches with {total_cells_needed} total cells"
        )

        # Compute random baseline using existing entropy calculation methods
        print("  - Computing random baseline entropy metrics...")

        # Compute within-batch entropy for random batches
        random_within_batch_metrics = self.entropy_calc.compute_within_batch_entropy(
            random_batch_ids
        )

        # Compute across-batch entropy for random batches
        random_across_batch_metrics = self.entropy_calc.compute_across_batch_entropy(
            random_batch_ids
        )

        random_result = EntropyMetrics(
            strategy="random",
            within_batch_l1=random_within_batch_metrics["l1_distance"],
            across_batch_l1=random_across_batch_metrics["l1_distance"],
            n_batches=len(random_batch_ids),
            total_cells=len(random_batch_ids) * 32,
            measurement_time=0.0,  # Not measuring time for baseline
        )
        results.append(random_result)

        return results

    def print_results(self, results: list[EntropyMetrics]):
        """Print entropy measurement results with normalized scores."""
        print("\n" + "=" * 80)
        print("ENTROPY MEASUREMENT RESULTS")
        print("=" * 80)

        # Print raw metrics
        print("\nRaw Entropy Metrics:")
        print("-" * 80)
        print(f"{'Strategy':<15} {'Within-Batch L1':<15} {'Across-Batch L1':<15}")
        print("-" * 80)

        for result in results:
            print(
                f"{result.strategy:<15} {result.within_batch_l1:<15.1f} "
                f"{result.across_batch_l1:<15.1f}"
            )

        # Find random baseline for normalization
        random_result = None
        for result in results:
            if result.strategy == "random":
                random_result = result
                break

        if random_result is None:
            print("\n⚠️  No random baseline found, skipping normalized scores")
            return

        # Print normalized scores
        print("\nNormalized Entropy Scores [0=Sequential, 1=Random]:")
        print("-" * 80)
        print(f"{'Strategy':<15} {'Within-Batch L1':<15} {'Across-Batch L1':<15}")
        print("-" * 80)

        for result in results:
            if result.strategy == "random":
                continue  # Skip random in normalized results

            # Normalize scores using sequential and random baselines
            random_result = next(r for r in results if r.strategy == "random")
            sequential_result = next(r for r in results if r.strategy == "sequential")

            # Use existing normalize_score function for within-batch
            l1_score = self.entropy_calc.normalize_score(
                result.within_batch_l1,
                sequential_result.within_batch_l1,
                random_result.within_batch_l1,
            )

            # Use existing normalize_score function for across-batch
            batch_l1_score = self.entropy_calc.normalize_score(
                result.across_batch_l1,
                sequential_result.across_batch_l1,
                random_result.across_batch_l1,
            )

            print(f"{result.strategy:<15} {l1_score:<15.3f} {batch_l1_score:<15.3f}")

        print("\n" + "=" * 80)
        print("INTERPRETATION GUIDE:")
        print("• Within-Batch: How random are the cells within each batch")
        print("• Across-Batch: How much batch composition changes between batches")
        print("• L1 Distance: Mean absolute difference between cell ID pairs")
        print("• Sequential: Contiguous cell IDs from Lance batches")
        print("• Fragment: Complete Lance fragments for higher entropy")
        print("• MoS: Mixture of Scanners for maximum entropy")
        print("• Random: Pseudo-random baseline for comparison")
        print("• Scores closer to 0 = more sequential, closer to 1 = more random")


def main():
    """Run entropy measurement for different loading strategies."""

    # Load SLAF array
    print("Loading SLAF array from ../slaf-datasets/plate1_Tahoe100M_v21.slaf...")
    slaf_array = SLAFArray("../slaf-datasets/plate1_Tahoe100M_v21.slaf")
    print(f"Dataset has {slaf_array.cells.count_rows():,} cells")

    # Create entropy benchmark
    benchmark = EntropyBenchmark(slaf_array)

    # Run entropy measurement for all strategies
    results = benchmark.benchmark_all_strategies(n_batches=10000)

    # Print results
    benchmark.print_results(results)

    print("\n✅ Entropy measurement complete!")


if __name__ == "__main__":
    main()
