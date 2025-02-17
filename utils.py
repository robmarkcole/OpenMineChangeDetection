"""
Author: Austin Dibble

This file falls under the repository's OSL 3.0.

"""

import torch
import matplotlib.pyplot as plt
from typing import Dict, Optional
from torchvision.transforms.functional import to_pil_image
from torchgeo.datasets.utils import draw_semantic_segmentation_masks
import numpy as np
import kornia.augmentation as K
from tqdm import tqdm
from .TinyCD.metrics.metric_tool import ConfuseMatrixMeter
import requests
import hashlib
import os
import torchmetrics
from .datasets import OMS2CD
import matplotlib.cm as cm
from scipy import interpolate
import pandas as pd

def plot_prediction(
    model: torch.nn.Module,
    sample,
    bands: str,
    colormap: str = "blue",
    threshold: float = 0.5,
    alpha: float = 0.4,
    show_titles: bool = True,
    suptitle: Optional[str] = None,
) -> None:
    """
    Plot the prediction results for a given model on a sample image.

    Parameters:
        model (torch.nn.Module): The pre-trained or trained PyTorch model for prediction.
        sample (Dict[str, torch.Tensor]): The sample data containing 'image' and 'mask' tensors.
        bands (str): Specify which bands to use for visualization. Options: "all" or "rgb".
        colormap (str, optional): The colormap to use for the predicted mask visualization. Default is "blue".
        threshold (float, optional): The threshold value for binarizing the predicted mask. Default is 0.5.
        alpha (float, optional): The alpha value for mask overlay on the image. Default is 0.4.
        show_titles (bool, optional): Whether to show titles for each subplot. Default is True.
        suptitle (str, optional): The title for the entire plot. Default is None.

    Returns:
        None: The function plots the prediction results but does not return anything.

    Example:
        # Assuming the model, sample data, and other parameters are defined.
        plot_prediction(model, sample_data, bands="rgb", colormap="blue", threshold=0.5,
                        alpha=0.4, show_titles=True, suptitle="Prediction Results")
    """
    model = model.eval()
    with torch.no_grad():
        img = sample['image']
        if len(img.shape) < 4:
            img = img.unsqueeze(0)
        mask_pred = model(img).squeeze(1).cpu()

    if len(img.shape) > 3:
        img = img.squeeze(0)
    mask_pred = (mask_pred > threshold).float()

    rgb_inds = [3, 2, 1] if bands == "all" else [0, 1, 2]

    def get_masked(img: torch.Tensor, mask: torch.Tensor) -> "np.typing.NDArray[np.uint8]":
        rgb_img = img[rgb_inds].float().cpu().numpy()
        per02 = np.percentile(rgb_img, 2)
        per98 = np.percentile(rgb_img, 98)
        rgb_img = (np.clip((rgb_img - per02) / (per98 - per02), 0, 1) * 255).astype(
            np.uint8
        )
        array: "np.typing.NDArray[np.uint8]" = draw_semantic_segmentation_masks(
            torch.from_numpy(rgb_img),
            mask,
            alpha=alpha,
            colors=colormap,
        )
        return array

    idx = img.shape[0] // 2
    image1 = get_masked(img[:idx], sample["mask"])
    image2 = get_masked(img[idx:], sample["mask"])
    image3 = get_masked(img[:idx], mask_pred)
    image4 = get_masked(img[idx:], mask_pred)
    image5 = to_pil_image(mask_pred.byte())
    image6 = to_pil_image(sample["mask"].byte())

    fig, axs = plt.subplots(3, 2, figsize=(10, 15))
    axs[0, 0].imshow(image1)
    axs[0, 0].axis("off")
    axs[0, 1].imshow(image2)
    axs[0, 1].axis("off")
    axs[1, 0].imshow(image3)
    axs[1, 0].axis("off")
    axs[1, 1].imshow(image4)
    axs[1, 1].axis("off")
    axs[2, 0].imshow(image5)
    axs[2, 0].axis("off")
    axs[2, 1].imshow(image6)
    axs[2, 1].axis("off")

    if show_titles:
        axs[0, 0].set_title("Pre change")
        axs[0, 1].set_title("Post change")
        axs[1, 0].set_title("Pre change with predicted mask")
        axs[1, 1].set_title("Post change with predicted mask")
        axs[2, 0].set_title("Predicted mask")
        axs[2, 1].set_title("Ground truth mask")

    if suptitle is not None:
        plt.suptitle(suptitle)

    plt.show()

def test_TinyCD(model, device, datamodule, threshold=0.4):
    """
    Evaluate the TinyCD model on the test dataset and compute evaluation metrics.

    Parameters:
        model (torch.nn.Module): The pre-trained or trained TinyCD model.
        device (torch.device): The device (CPU or GPU) to run the evaluation on.
        datamodule: The datamodule containing the test dataloader.
        threshold (float, optional): The threshold value for binarizing the generated mask. Default is 0.4.

    Returns:
        dict: A dictionary containing evaluation metrics such as accuracy, precision, recall, F1-score, etc.

    Example:
        # Assuming the TinyCD model, device, and datamodule are defined.
        scores_dict = test_TinyCD(model, device, datamodule, threshold=0.5)
        print(scores_dict)

    License:  Some of this function's code was copied from TinyCD/training.py and TinyCD/test_ondata.py
    """
    bce_loss = 0.0
    criterion = torch.nn.BCELoss()

    # tool for metrics
    tool_metric = ConfuseMatrixMeter(n_class=2)
    model = model.eval()
    model = model.to(device)
    test_loader = datamodule.test_dataloader()
    with torch.no_grad():
        for img_dict in tqdm(test_loader):
            img_dict = datamodule.aug(img_dict)
            # test in the model
            generated_mask = model(img_dict['image'].to(device)).squeeze(1)
            bce_loss += criterion(generated_mask, img_dict['mask'].to(device).float())
            bin_genmask = (generated_mask > threshold)
            bin_genmask = bin_genmask.cpu().numpy().astype(int)
            mask = img_dict['mask'].cpu().numpy().astype(int)
            tool_metric.update_cm(pr=bin_genmask, gt=mask)

        bce_loss /= len(test_loader)
        
        scores_dictionary = tool_metric.get_scores()
        scores_dictionary['loss'] = bce_loss
        return scores_dictionary


def download_file(url, save_path):
    """
    Download a file from a given URL and save it to the specified path.

    Parameters:
        url (str): The URL of the file to download.
        save_path (str): The path where the downloaded file will be saved.

    Returns:
        None

    Example:
        # Assuming the URL and save_path are defined.
        download_file(url, save_path)
    """
    response = requests.get(url, stream=True)
    file_size = int(response.headers.get("Content-Length", 0))
    progress = tqdm(total=file_size, unit="B", unit_scale=True, unit_divisor=1024, desc=f'Downloading file to {save_path}')

    with open(save_path, 'wb') as file:
        for data in response.iter_content(chunk_size=1024):
            if data:
                file.write(data)
                # Update the progress bar manually
                progress.update(len(data))

    progress.close()

def compute_sha256(file_path):
    """
    Compute the SHA-256 hash of a file.

    Parameters:
        file_path (str): The path to the file for which the SHA-256 hash will be computed.

    Returns:
        str: The computed SHA-256 hash of the file in hexadecimal format.

    Example:
        # Assuming the file_path is defined.
        hash_value = compute_sha256(file_path)
    """
    sha256_hash = hashlib.sha256()
    with open(file_path,"rb") as f:
        # Read and update hash in chunks of 4K
        for byte_block in iter(lambda: f.read(4096),b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def verify_file(save_path, good_hash):
    """
    Verify file's SHA-256 hash with the given good_hash.

    Parameters:
        save_path (str): The path to the file to be verified.
        good_hash (str): The expected SHA-256 hash string.

    Returns:
        bool: True if the computed SHA-256 hash matches the good_hash, False otherwise.
    """
    if os.path.isfile(save_path) and compute_sha256(save_path) == good_hash:
        return True
    else:
        return False

def download_and_verify(url, save_path, good_hash):
    download_file(url, save_path)
    print(f'Verifying hashes: ', end='')
    verified = verify_file(save_path, good_hash)
    print(f"{'Good.' if verified else 'Hashes do not match.'}")
    return verified

def iou_score(output, target, threshold, apply_sigmoid=False):
    smooth = 1e-6
    if apply_sigmoid:
        output = torch.sigmoid(output)
    output_ = output > threshold
    target_ = target > threshold
    intersection = (output_ & target_).sum(dim=(2, 3))
    union = (output_ | target_).sum(dim=(2, 3))
    iou = (intersection + smooth) / (union + smooth)

    return iou.sum(), iou.numel()

def evaluate_model(model, dataloader, proc_func, device, threshold=0.3, pr_thresholds=50):
    # Create a DataLoader
    accuracy = torchmetrics.Accuracy(task='binary', threshold=threshold).to(device)
    f1 = torchmetrics.F1Score(task='binary', threshold=threshold).to(device)
    recall = torchmetrics.Recall(task='binary', threshold=threshold).to(device)
    precision = torchmetrics.Precision(task='binary', threshold=threshold).to(device)
    average_precision = torchmetrics.AveragePrecision(task='binary').to(device)
    if pr_thresholds is not None:
        pr_curve = torchmetrics.PrecisionRecallCurve(task='binary', thresholds=pr_thresholds).to(device)
    else:
        pr_curve = torchmetrics.PrecisionRecallCurve(task='binary').to(device)

    total_iou = 0
    total_images = 0
    # Ensure model is in evaluation mode
    model.eval()

    with torch.no_grad():
        for batch in tqdm(dataloader):
            # Apply preprocessing function
            outputs, targets = proc_func(model, batch, device)
            if len(outputs.shape) == 3:
                outputs = outputs.unsqueeze(1)
            if len(targets.shape) == 3:
                targets = targets.unsqueeze(1)
            accuracy.update(outputs, targets)
            f1.update(outputs, targets)
            recall.update(outputs, targets)
            precision.update(outputs, targets)
            average_precision.update(outputs, targets)
            pr_curve.update(outputs, targets)
            iou_sum, num_images = iou_score(outputs, targets, threshold)
            total_iou += iou_sum.item()
            total_images += num_images

    # Compute final metrics
    metrics = {
        'OA': accuracy.compute().item(),
        'F1': f1.compute().item(),
        'recall': recall.compute().item(),
        'precision': precision.compute().item(),
        'AP': average_precision.compute().item(),
        'PRC': pr_curve.compute(),
        'IoU': total_iou / total_images,
    }

    return metrics

def get_mask_preds_tinycd(model, batch, device):
    normalize_sample = OMS2CD.GetNormalizeTransform(bands='rgb')
    normalize_batch = K.Normalize(mean=normalize_sample.mean, std=normalize_sample.std)
    batch = {k: v.to(device) for k,v in batch.items() if isinstance(v, torch.Tensor)}
    batch['image'] = normalize_batch(batch['image'])
    mask_pred = model(batch['image']).squeeze(1)
    mask_target = batch['mask']
    return mask_pred, mask_target

def get_mask_preds_lsnet(model, batch, device):
    normalize_sample = OMS2CD.GetNormalizeTransform(bands='rgb')
    normalize_batch = K.Normalize(mean=normalize_sample.mean, std=normalize_sample.std)
    batch = {k: v.to(device) for k,v in batch.items() if isinstance(v, torch.Tensor)}
    batch['image'] = normalize_batch(batch['image'])
    mask_pred = model(batch)
    mask_pred = mask_pred[-1]
    mask_pred_prob = torch.softmax(mask_pred, dim=1)
    # mask_pred_val, mask_pred_idx = torch.max(mask_pred, 1)
    mask_pred_prob_class1 = mask_pred_prob[:, 1, :, :]
    return mask_pred_prob_class1, batch['mask']

def get_mask_preds_ddpmcd(model, batch, device):
    normalize_sample = OMS2CD.GetNormalizeTransform(bands='rgb')
    normalize_batch = K.Normalize(mean=normalize_sample.mean, std=normalize_sample.std)
    batch = {k: v.to(device) for k,v in batch.items() if isinstance(v, torch.Tensor)}
    batch['image'] = normalize_batch(batch['image'])
    mask_pred = model(batch)
    # mask_pred = torch.argmax(mask_pred, dim=1, keepdim=False)
    mask_pred_prob = torch.softmax(mask_pred, dim=1)
    # mask_pred_val, mask_pred_idx = torch.max(mask_pred, 1)
    mask_pred_prob_class1 = mask_pred_prob[:, 1, :, :]
    return mask_pred_prob_class1, batch['mask']

def load_tinycd(weight_path, device):
    from .TinyCD.models.cd_lightning import ChangeDetectorLightningModule
    tinycd = ChangeDetectorLightningModule(freeze_backbone=False)
    tinycd.load_state_dict(torch.load(weight_path, map_location=device))
    return tinycd.to(device).eval()

def load_lsnet(weight_path, device):
    from .LSNet import LSNetLightning
    from .LSNet.utils.parser import get_parser_with_args
    class AttributeDict(object):
        def __init__(self, dictionary):
            for key, value in dictionary.items():
                setattr(self, key, value)

    state = torch.load(weight_path, map_location=device)
    opt = get_parser_with_args(metadata_json='OpenMineChangeDetection/LSNet/metadata.json')
    opt = AttributeDict(opt)
    model = LSNetLightning(opt)
    model.load_state_dict(state, strict=False)
    return model.to(device).eval()

def load_ddpmcd(weight_path, device):
    from .ddpm_cd.core import logger as Logger
    from .ddpm_cd.ddpm_lightning import CD

    class Args:
        def __init__(self):
            self.config = 'OpenMineChangeDetection/ddpm_cd/config/oms2cd.json'
            self.phase = 'train'
            self.gpu_ids = '0'
            self.debug = False
            self.enable_wandb = False
            self.log_eval = False

    opt = Logger.parse(Args())
    opt = Logger.dict_to_nonedict(opt)
    change_detection = CD(opt)
    if opt['path_cd']['finetune_path'] is not None:
        change_detection.load_network()
    change_detection.load_state_dict(torch.load(weight_path, map_location=device))
    return change_detection.to(device).eval()

def load_ddpmcd_oms2cd(device, download_cache_dir='ddpmcd_weights'):
    """
    Load DDPM-CD trained on OMS2CD. Note that this will download the trained weights from GDrive.
    """
    good_hash = "ecf10f6ae54aa7e19814f3797de025d7fbfd3b957547666f8111094b41e71a18"
    oms2cd_file = os.path.join(download_cache_dir, "ddpmcd_oms2cd.pt")
    os.makedirs(download_cache_dir, exist_ok=True)
    if not os.path.isfile(oms2cd_file):
        assert download_and_verify("https://drive.google.com/uc?export=download&id=1CeQJQKjF8oMSUQs7P_mSVHwzthB9BSuy&confirm=t&uuid=2b47d772-4d26-4f8a-84c9-ae6f70b60ddf&at=ALt4Tm3nYgdnLO8B4zys6up4Hdh3:1691262922716", 
                                    oms2cd_file, 
                                    good_hash)
    
    return load_ddpmcd(oms2cd_file, device)

def load_tinycd_oms2cd(device):
    """
    Load TinyCD trained on OMS2CD. Note that this will only work properly when running in Colab since it expects the path to the weights to include OpenMineChangeDetection. 
    If needed, uses load_tinycd instead to specify your own path.
    The trained weights are in OpenMineChangeDetection/final_weights/tinycd_oms2cd.pt
    """
    return load_tinycd(os.path.join('OpenMineChangeDetection', 'final_weights', 'tinycd_oms2cd.pt'), device)

def load_lsnet_oms2cd(device):
    """
    Load LSNet trained on OMS2CD. Note that this will only work properly when running in Colab since it expects the path to the weights to include OpenMineChangeDetection. 
    If needed, uses load_tinycd instead to specify your own path.
    The trained weights are in OpenMineChangeDetection/final_weights/lsnet_oms2cd.pt
    """
    return load_lsnet(os.path.join('OpenMineChangeDetection', 'final_weights', 'lsnet_oms2cd.pt'), device)

def download_prep_oms2cd(output_dir):
    import zipfile
    import os
    good_hash = "c0170a57f4510f00338ee6a1484019c88c66ed3cbaa2840e17ff554f18b0b185"
    oms2cd_file = os.path.join(output_dir, 'OMS2CD.zip')

    if os.path.isdir(output_dir):
        print(f'Output directory {output_dir} already exists. Skipping dataset prep.')
        return

    os.makedirs(output_dir)
    if not os.path.isfile(oms2cd_file):
        assert download_and_verify("https://drive.google.com/uc?export=download&id=1Kyle3U-lHQsj_zo7xO-GQJk_ZX9SmiKG&confirm=t&uuid=1756e9bd-f27e-46a7-a69d-99808107f180&at=AB6BwCAIA-RdbjvQOtJyPzNBDD9S:1691856890025", 
                                    oms2cd_file, 
                                    good_hash)
    else:
        print(f'Archive {oms2cd_file} already downloaded. Skipping.')

    print(f'Extracting archive into {output_dir}.')
    with zipfile.ZipFile(oms2cd_file, 'r') as zip_ref:
        zip_ref.extractall(output_dir)

    print(f'Removing .zip file.')
    os.remove(oms2cd_file) # missing_ok=True

    print(f'Done.')

def plot_pr_curve(prc):
    precision, recall, thresholds = prc
    precision = precision.cpu().numpy()
    recall = recall.cpu().numpy()
    thresholds = thresholds.cpu().numpy()

    # Append a maximum value to thresholds to make it the same length as precision and recall
    thresholds = np.append(thresholds, np.max(thresholds))
    df = pd.DataFrame({'recall': recall, 'precision': precision, 'thresholds': thresholds})
    df = df.groupby('recall', as_index=False).mean()

    # Interpolate the precision, recall, and thresholds
    num_interp_points = 100  # Change this to control the number of interpolation points
    interp_recall = np.linspace(df['recall'].min(), df['recall'].max(), num_interp_points)
    interp_precision_func = interpolate.interp1d(df['recall'], df['precision'], kind='cubic')
    interp_precision = interp_precision_func(interp_recall)
    interp_thresholds_func = interpolate.interp1d(df['recall'], df['thresholds'], kind='cubic')
    interp_thresholds = interp_thresholds_func(interp_recall)
    plt.figure()

    norm = plt.Normalize(interp_thresholds.min(), interp_thresholds.max())
    cmap = cm.get_cmap('viridis')

    # Plot the precision-recall curve, color by threshold
    for i in range(len(interp_precision)):
        plt.plot(interp_recall[i], interp_precision[i], marker='.', color=cmap(norm(interp_thresholds[i])))

    sm = cm.ScalarMappable(norm=norm, cmap=cmap)
    plt.colorbar(sm, label='Threshold')
    plt.xlabel('Recall')
    plt.ylabel('Precision')
    plt.title('Precision-Recall Curve')
    plt.show()