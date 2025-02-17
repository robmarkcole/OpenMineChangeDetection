"""
This file includes code derived from the torchgeo project,
which is licensed under the MIT License. The original license notice
is included below:

MIT License

Copyright (c) Microsoft Corporation.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE

"""

"""
The majority of the code in this file is not derived from torchgeo, and is licensed
under OSL 3.0, as described in the README.
"""


from IPython.core.interactiveshell import import_item
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

"""OSCD dataset."""
from collections.abc import Sequence
from typing import Any, Callable, Optional, Union, Tuple, List

import numpy as np
import torch
from torch import Tensor
from PIL import Image
import os
from tiler import Tiler, Merger
from torch.utils.data import DataLoader

from torchgeo.datasets import NonGeoDataset
from torchgeo.datasets import OSCD
from torchvision.transforms import Normalize
from .transforms import NormalizeScale, NormalizeImageDict, TransformedSubset

import kornia.augmentation as K
from torchgeo.transforms import AugmentationSequential
from torchgeo.datamodules.geo import NonGeoDataModule
from torchgeo.datamodules.utils import dataset_split

class OSCD_Chipped(OSCD):
    normalisation_map = {
        "rgb": (torch.tensor([1298.9539, 1284.8223, 1365.5087]), torch.tensor([765.5303, 537.6539, 414.6901])),
        "all": (torch.tensor([1571.1372, 1365.5087, 1284.8223, 1298.9539, 1431.2260, 1860.9531,
                    2081.9634, 1994.7665, 2214.5986,  641.4485,   14.3672, 1957.3165,
                    1419.6107]), 
                torch.tensor([274.9591,  414.6901,  537.6539,  765.5303,  724.2261,  760.2133,
                    848.7888,  866.8081,  920.1696,  322.1572,    8.6878, 1019.1249,
                    872.1970])
              )
    }

    def __init__(
        self,
        root: str = "data",
        split: str = "train",
        bands: str = "rgb", # or "all"
        transforms: Optional[Callable[[dict[str, Tensor]], dict[str, Tensor]]] = None,
        download: bool = True,
        checksum: bool = False,
        stride: Union[int, Tuple[int, int], List[int]] = 128,
        tile_size: Union[int, Tuple[int, int], List[int]] = 256
    ) -> None:
        super().__init__(root, split, bands, transforms, download, checksum)
        if isinstance(tile_size, int):
            self.tile_shape = (tile_size, tile_size)
        elif isinstance(tile_size, (tuple, list)):
            assert len(tile_size) == 2
            self.tile_shape = tile_size.copy()

        if isinstance(stride, int):
            stride_shape = (stride, stride)
        elif isinstance(stride, (tuple, list)):
            assert len(stride) == 2
            stride_shape = stride.copy()
        
        self.tile_overlap = tuple(max(tile_size_dim - stride_dim, 0) for tile_size_dim, stride_dim in zip(self.tile_shape, stride_shape))
        self.total_dataset_length, self.chip_index_map, self.image_shapes_map = self._calculate_dataset_len()

    def _load_raw_index(self, index: int):
        files = self.files[index]
        image1 = self._load_image(files["images1"])
        image2 = self._load_image(files["images2"])
        mask = self._load_target(str(files["mask"]))
        return image1, image2, mask

    def _load_tensor_index(self, index: int):
        image1, image2, mask = self._load_raw_index(index)
        image1_tensor = torch.from_numpy(image1)
        image2_tensor = torch.from_numpy(image2)
        mask_tensor = torch.from_numpy(mask).to(torch.long)
        raw_img_tensor = torch.cat([image1_tensor, image2_tensor])
        return raw_img_tensor, mask_tensor

    def _calculate_dataset_len(self):
      """Returns the total length (number of chips) of the dataset
          This is the total number of tiles after tiling every high-res image
          in the dataset and calculating the tiling using the tiler.

          This function also creates and returns a dictionary that maps from the
          chipped dataset index to the original file index.

      Returns:
          - Length of the dataset in number of chips
          - Index map (key is the chip index, value is a tuple of (file index, chip index relative to the last file))
          - Map of image shapes
      """
      total_tiles = 0
      index_map = {}
      image_shapes = {}

      for i in range(len(self.files)):
          files = self.files[i]
          image1 = self._load_image(files["images1"])
          image1_tensor = torch.from_numpy(image1)
          raw_img_tensor = torch.cat([image1_tensor, image1_tensor])
          tiler = Tiler(data_shape=raw_img_tensor.shape,
                tile_shape=(raw_img_tensor.shape[0], self.tile_shape[0], self.tile_shape[1]),
                overlap=(raw_img_tensor.shape[0]-1, self.tile_overlap[0], self.tile_overlap[1]),
                mode="drop",
                channel_dimension=0)
          
          image_shapes[i] = list(raw_img_tensor.shape)
          tile_shape = tiler.get_mosaic_shape(with_channel_dim=True)
          num_tiles = tile_shape[0] * tile_shape[1] * tile_shape[2]
          # Map chip index to file index
          for j in range(total_tiles, total_tiles + num_tiles):
              index_map[j] = (i, j - total_tiles)

          total_tiles += num_tiles

      return total_tiles, index_map, image_shapes

    def __getitem__(self, chip_index: int) -> dict[str, Tensor]:
        """Return an index within the dataset.

        Args:
            chip_index: index to return

        Returns:
            data and label at that index
        """

        (file_index, relative_chip_index) = self.chip_index_map[chip_index]
        full_image_shape = self.image_shapes_map[file_index].copy()
        full_image_shape[0] = full_image_shape[0] + 1
        tiler = Tiler(data_shape=full_image_shape,
              tile_shape=(full_image_shape[0], self.tile_shape[0], self.tile_shape[1]),
              overlap=(full_image_shape[0]-1, self.tile_overlap[0], self.tile_overlap[1]),
              mode="drop",
              channel_dimension=0)

        # img, mask = self._load_tensor_index(file_index)
        img1, img2, mask = self._load_raw_index(file_index)
        mask = np.expand_dims(mask, 0)
        img_full = np.concatenate((img1, img2, mask))
        
        img_mask_tile = tiler.get_tile(img_full, relative_chip_index, copy_data = False)

        img_tile, mask_tile = img_mask_tile[0:full_image_shape[0] - 1, :, :], img_mask_tile[full_image_shape[0] - 1, :, :]
        img_tile_tensor = torch.from_numpy(img_tile)
        mask_tile_tensor = torch.from_numpy(mask_tile).to(torch.long).unsqueeze(0)

        # image = torch.cat([image1, image2])
        sample = {"image": img_tile_tensor, "mask": mask_tile_tensor}

        if self.transforms is not None:
            sample = self.transforms(sample)

        return sample

    def __len__(self) -> int:
        """Return the number of data points in the dataset.

        Returns:
            length of the dataset
        """
        return self.total_dataset_length

    def _load_image(self, paths: Sequence[str]) -> Tensor:
        """Load a single image.

        Args:
            path: path to the image

        Returns:
            the image
        """
        images: list["np.typing.NDArray[np.int_]"] = []
        for path in paths:
            with Image.open(path) as img:
                images.append(np.array(img))
        array: "np.typing.NDArray[np.int_]" = np.stack(images, axis=0).astype(np.int_)
        return array

    def _load_target(self, path: str) -> Tensor:
        """Load the target mask for a single image.

        Args:
            path: path to the image

        Returns:
            the target mask
        """
        filename = os.path.join(path)
        with Image.open(filename) as img:
            array: "np.typing.NDArray[np.int_]" = np.array(img.convert("L"))
            array = np.clip(array, a_min=0, a_max=1).astype(np.int_)
            return array

    def get_normalization_values(self):
        return OSCD_Chipped.GetNormalizationValues(self.bands)

    def split_images(self, images):
        n_bands = 3 if self.bands == "rgb" else 13
        pre, post = images[:, 0:n_bands], images[:, n_bands:2*n_bands]
        return pre, post

    def set_transforms(self, transforms):
        self.transforms = transforms

    @staticmethod
    def GetNormalizationValues(bands="rgb"):
        return OSCD_Chipped.normalisation_map[bands]

    @staticmethod
    def CalcMeanVar(root, split="train", bands="rgb"):
        def t(img_dict):
            return {'image': img_dict['image'].to(torch.float), 'mask': img_dict['mask']}

        dataset = OSCD(root=root, split=split, bands=bands, download=False, transforms = t)
        loader = DataLoader(dataset, batch_size=1, num_workers=0)

        def preproc_img(img_dict):
            images = img_dict['image']
            batch_samples = images.size(0)
            B, C, W, H = images.size()
            # Separate the tensor into two tensors of shape (B, 3, W, H)
            image1 = images[:, :(C//2), :, :]
            image2 = images[:, (C//2):, :, :]
            # Stack them to get a tensor of shape (2B, 3, W, H)
            images = torch.cat((image1, image2), dim=0)
            images = images.view(-1, C//2, W, H)
            return images

        def compute_dataset_mean_std(dataloader):
            ex_img = preproc_img(next(iter(dataloader))).shape[1]
            total_sum = torch.zeros(ex_img)
            total_sq_sum = torch.zeros(ex_img)
            total_num_pixels = 0

            for batch in dataloader:
                image = preproc_img(batch).float()
                total_sum += image.sum(dim=[0, 2, 3])  # sum of pixel values in each channel
                total_sq_sum += (image ** 2).sum(dim=[0, 2, 3])  # sum of squared pixel values in each channel
                total_num_pixels += image.shape[0] * image.shape[2] * image.shape[3]  # total number of pixels in an image

            mean = total_sum / total_num_pixels  # mean = total sum / total number of pixels
            std = (total_sq_sum / total_num_pixels - mean ** 2) ** 0.5  # std = sqrt(E[X^2] - E[X]^2)

            return mean, std
        return compute_dataset_mean_std(loader)

    @staticmethod
    def GetScaleTransform():
        return NormalizeScale(scale_factor=10000)
    
    @staticmethod
    def GetNormalizeTransform(bands="rgb"):
        # Mean/STD as 3 or 13 channels/bands
        normalisation = OSCD_Chipped.GetNormalizationValues(bands)
        # We are loading our dataset as a stack of two images (pre/post) as 2 *
        # the number of channels in one image. So for RGB, our tensor has 6
        # channels instead of 2. So we need to stack our normalisation tensor
        # so it matches the image tensor.
        mean, std = torch.tensor(normalisation[0]), torch.tensor(normalisation[1])
        mean, std = torch.cat((mean, mean), dim=0), torch.cat((std, std), dim=0)
        return NormalizeImageDict(mean=mean, std=std)

# Adapted from https://torchgeo.readthedocs.io/en/latest/_modules/torchgeo/datamodules/oscd.html#OSCDDataModule
class OSCDDataModule(NonGeoDataModule):
    """LightningDataModule implementation for the OSCD dataset.

    Uses the train/test splits from the dataset and further splits
    the train split into train/val splits.

    .. versionadded:: 0.2
    """
    mean = OSCD_Chipped.normalisation_map['all'][0]
    std = OSCD_Chipped.normalisation_map['all'][1]

    def __init__(
        self,
        batch_size: int = 64,
        val_split_pct: float = 0.2,
        num_workers: int = 0,
        **kwargs: Any,
    ) -> None:
        """Initialize a new OSCDDataModule instance.

        Args:
            batch_size: Size of each mini-batch.
            patch_size: Size of each patch, either ``size`` or ``(height, width)``.
                Should be a multiple of 32 for most segmentation architectures.
            val_split_pct: Percentage of the dataset to use as a validation set.
            num_workers: Number of workers for parallel data loading.
            **kwargs: Additional keyword arguments passed to
                :class:`~torchgeo.datasets.OSCD`.
        """
        super().__init__(OSCD_Chipped, batch_size, num_workers, **kwargs)

        self.val_split_pct = val_split_pct

        # self.bands = kwargs.get("bands", "all")
        self.bands = "rgb" # or "all"
        if self.bands == "rgb":
            self.mean = self.mean[[3, 2, 1]]
            self.std = self.std[[3, 2, 1]]

        # Change detection, 2 images from different times
        self.mean = torch.cat([self.mean, self.mean], dim=0)
        self.std = torch.cat([self.std, self.std], dim=0)

        self.aug = AugmentationSequential(
            K.Normalize(mean=self.mean, std=self.std),
            # _RandomNCrop(self.patch_size, batch_size),
            data_keys=["image", "mask"],
        )
        self.transforms = {stage: None for stage in ['fit', 'validate', 'test', 'predict']}

    def setup(self, stage: str) -> None:
        """Set up datasets.

        Args:
            stage: Either 'fit', 'validate', 'test', or 'predict'.
        """
        if stage in ["fit", "validate"]:
            self.dataset = OSCD_Chipped(split="train", **self.kwargs)
            self.train_dataset, self.val_dataset = dataset_split(
                self.dataset, val_pct=self.val_split_pct
            )
            self.train_dataset = TransformedSubset(self.train_dataset, self.transforms['fit'])
            self.val_dataset = TransformedSubset(self.val_dataset, self.transforms['validate'])
        if stage in ["test"]:
            self.test_dataset = OSCD_Chipped(split="test", **self.kwargs)
            self.test_dataset.set_transforms(self.transforms['test'])

    def set_transforms(self, transforms: Any, stage: str = "fit"):
        assert stage in ['fit', 'validate', 'test', 'predict']
        self.transforms[stage] = transforms