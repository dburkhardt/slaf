from slaf.core.slaf import SLAFArray
from slaf.data import SLAFConverter
from slaf.ml.dataloaders import SLAFDataLoader
from bionemo.scdl.io.single_cell_memmap_dataset import SingleCellMemMapDataset

from benchmark_dataloaders_external import ExternalDataloaderBenchmark
import os, urllib.request, argparse, shutil



def prepare_datasets(clean: bool = False):
    data_dir = os.path.expanduser("~/.cache/slaf/")
    if clean and os.path.exists(data_dir):
        shutil.rmtree(data_dir)
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    H5AD_PATH = os.path.join(data_dir, "cellxgene_example_25k.h5ad")
    SLAF_PATH = os.path.join(data_dir, "cellxgene_example_25k.slaf")
    SCDL_PATH = os.path.join(data_dir, "cellxgene_example_25k")
    
    source_url = "https://datasets.cellxgene.cziscience.com/97e96fb1-8caf-4f08-9174-27308eabd4ea.h5ad"
    urllib.request.urlretrieve(source_url, H5AD_PATH)

    SLAF_PATH = os.path.join(data_dir, "cellxgene_example_25k.slaf")
    SCDL_PATH = os.path.join(data_dir, "cellxgene_example_25k")

    # Convert an h5ad file to SLAF format
    if not os.path.exists(SLAF_PATH):
        converter = SLAFConverter()
        converter.convert(H5AD_PATH, SLAF_PATH)

    # Convert the h5ad file to SCDL format
    if not os.path.exists(SCDL_PATH):
        dataset = SingleCellMemMapDataset(SCDL_PATH, H5AD_PATH)
        dataset.save()

    return H5AD_PATH, SLAF_PATH, SCDL_PATH

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="Clean cached datasets")
    args = parser.parse_args()
    
    SLAF_PATH, SCDL_PATH = prepare_datasets(args.clean)

        # Run the benchmarks
    bench = ExternalDataloaderBenchmark(
        slaf_path=SLAF_PATH,
        h5ad_path=None,  # Not needed for SLAF and SCDL comparison
        tiledb_path=None,
        scdl_path=SCDL_PATH,
    )

    # Keep just SLAF and BioNeMo SCDL
    bench.competitor_configs = {
        "SLAF": {
            "tier1": {"raw_mode": True, "batch_size": 64},
            "tier2": {"raw_mode": False, "batch_size": 64},
            "processes": 1,
        },
        "BioNeMo SCDL": {
            "tier1": {"raw_mode": True, "batch_size": 64},
            "tier2": {"raw_mode": True, "batch_size": 64},
            "processes": 1,
        },
    }

    results = bench.run_benchmarks()
    bench.print_results(results)

if __name__ == "__main__":
    main()