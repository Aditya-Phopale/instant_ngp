#!/usr/bin/env python3

# Copyright (c) 2020-2022, NVIDIA CORPORATION.  All rights reserved.
#
# NVIDIA CORPORATION and its licensors retain all intellectual property
# and proprietary rights in and to this software, related documentation
# and any modifications thereto.  Any use, reproduction, disclosure or
# distribution of this software and related documentation without an express
# license agreement from NVIDIA CORPORATION is strictly prohibited.

import argparse
import os
import commentjson as json

import numpy as np

import orjson
import torch

import shutil
import time

from instant_ngp.scripts.common import *
from instant_ngp.scripts.scenes import *

from tqdm import tqdm

import pyngp as ngp # noqa

def parse_args():
	parser = argparse.ArgumentParser(description="Run instant neural graphics primitives with additional configuration & output options")

	parser.add_argument("files", nargs="*", help="Files to be loaded. Can be a scene, network config, snapshot, camera path, or a combination of those.")
	parser.add_argument("--save_dir", default="", help="save dir for saving rendered RGB vals from test transform file")

	parser.add_argument("--scene", "--training_data", default="", help="The scene to load. Can be the scene's name or a full path to the training data. Can be NeRF dataset, a *.obj/*.stl mesh for training a SDF, an image, or a *.nvdb volume.")
	parser.add_argument("--mode", default="", type=str, help=argparse.SUPPRESS) # deprecated
	parser.add_argument("--network", default="", help="Path to the network config. Uses the scene's default if unspecified.")

	parser.add_argument("--load_snapshot", "--snapshot", default="", help="Load this snapshot before training. recommended extension: .ingp/.msgpack")
	parser.add_argument("--save_snapshot", default="", help="Save this snapshot after training. recommended extension: .ingp/.msgpack")

	parser.add_argument("--nerf_compatibility", action="store_true", help="Matches parameters with original NeRF. Can cause slowness and worse results on some scenes, but helps with high PSNR on synthetic scenes.")
	parser.add_argument("--test_transforms", default="", help="Path to a nerf style transforms json from which we will compute PSNR.")
	parser.add_argument("--near_distance", default=-1, type=float, help="Set the distance from the camera at which training rays start for nerf. <0 means use ngp default")
	parser.add_argument("--exposure", default=0.0, type=float, help="Controls the brightness of the image. Positive numbers increase brightness, negative numbers decrease it.")

	parser.add_argument("--screenshot_transforms", default="", help="Path to a nerf style transforms.json from which to save screenshots.")
	parser.add_argument("--screenshot_frames", nargs="*", help="Which frame(s) to take screenshots of.")
	parser.add_argument("--screenshot_dir", default="", help="Which directory to output screenshots to.")
	parser.add_argument("--screenshot_spp", type=int, default=16, help="Number of samples per pixel in screenshots.")

	parser.add_argument("--video_camera_path", default="", help="The camera path to render, e.g., base_cam.json.")
	parser.add_argument("--video_camera_smoothing", action="store_true", help="Applies additional smoothing to the camera trajectory with the caveat that the endpoint of the camera path may not be reached.")
	parser.add_argument("--video_fps", type=int, default=60, help="Number of frames per second.")
	parser.add_argument("--video_n_seconds", type=int, default=1, help="Number of seconds the rendered video should be long.")
	parser.add_argument("--video_render_range", type=int, nargs=2, default=(-1, -1), metavar=("START_FRAME", "END_FRAME"), help="Limit output to frames between START_FRAME and END_FRAME (inclusive)")
	parser.add_argument("--video_spp", type=int, default=8, help="Number of samples per pixel. A larger number means less noise, but slower rendering.")
	parser.add_argument("--video_output", type=str, default="video.mp4", help="Filename of the output video (video.mp4) or video frames (video_%%04d.png).")

	parser.add_argument("--save_mesh", default="", help="Output a marching-cubes based mesh from the NeRF or SDF model. Supports OBJ and PLY format.")
	parser.add_argument("--marching_cubes_res", default=256, type=int, help="Sets the resolution for the marching cubes grid.")
	parser.add_argument("--marching_cubes_density_thresh", default=2.5, type=float, help="Sets the density threshold for marching cubes.")

	parser.add_argument("--width", "--screenshot_w", type=int, default=0, help="Resolution width of GUI and screenshots.")
	parser.add_argument("--height", "--screenshot_h", type=int, default=0, help="Resolution height of GUI and screenshots.")

	parser.add_argument("--gui", action="store_true", help="Run the testbed GUI interactively.")
	parser.add_argument("--train", action="store_true", help="If the GUI is enabled, controls whether training starts immediately.")
	parser.add_argument("--n_steps", type=int, default=-1, help="Number of steps to train for before quitting.")
	parser.add_argument("--second_window", action="store_true", help="Open a second window containing a copy of the main output.")
	parser.add_argument("--vr", action="store_true", help="Render to a VR headset.")

	parser.add_argument("--sharpen", default=0, help="Set amount of sharpening applied to NeRF training images. Range 0.0 to 1.0.")


	return parser.parse_args()

def get_scene(scene):
	for scenes in [scenes_sdf, scenes_nerf, scenes_image, scenes_volume]:
		if scene in scenes:
			return scenes[scene]
	return None

if __name__ == "__main__":
	args = parse_args()
	if args.vr: # VR implies having the GUI running at the moment
		args.gui = True

	if args.mode:
		print("Warning: the '--mode' argument is no longer in use. It has no effect. The mode is automatically chosen based on the scene.")

	testbed = ngp.Testbed()
	testbed.root_dir = ROOT_DIR

	for file in args.files:
		scene_info = get_scene(file)
		if scene_info:
			file = os.path.join(scene_info["data_dir"], scene_info["dataset"])
		testbed.load_file(file)

	if args.scene:
		scene_info = get_scene(args.scene)
		if scene_info is not None:
			args.scene = os.path.join(scene_info["data_dir"], scene_info["dataset"])
			if not args.network and "network" in scene_info:
				args.network = scene_info["network"]

		testbed.load_training_data(args.scene)

	if args.gui:
		# Pick a sensible GUI resolution depending on arguments.
		sw = args.width or 1920
		sh = args.height or 1080
		while sw * sh > 1920 * 1080 * 4:
			sw = int(sw / 2)
			sh = int(sh / 2)
		testbed.init_window(sw, sh, second_window=args.second_window)
		if args.vr:
			testbed.init_vr()


	if args.load_snapshot:
		scene_info = get_scene(args.load_snapshot)
		if scene_info is not None:
			args.load_snapshot = default_snapshot_filename(scene_info)
		testbed.load_snapshot(args.load_snapshot)
	elif args.network:
		testbed.reload_network_from_file(args.network)

	ref_transforms = {}
	if args.screenshot_transforms: # try to load the given file straight away
		print("Screenshot transforms from ", args.screenshot_transforms)
		with open(args.screenshot_transforms) as f:
			ref_transforms = json.load(f)

	if testbed.mode == ngp.TestbedMode.Sdf:
		testbed.tonemap_curve = ngp.TonemapCurve.ACES

	testbed.nerf.sharpen = float(args.sharpen)
	testbed.exposure = args.exposure
	testbed.shall_train = args.train if args.gui else True


	testbed.nerf.render_with_lens_distortion = True

	network_stem = os.path.splitext(os.path.basename(args.network))[0] if args.network else "base"
	if testbed.mode == ngp.TestbedMode.Sdf:
		setup_colored_sdf(testbed, args.scene)

	if args.near_distance >= 0.0:
		print("NeRF training ray near_distance ", args.near_distance)
		testbed.nerf.training.near_distance = args.near_distance

	if args.nerf_compatibility:
		print(f"NeRF compatibility mode enabled")

		# Prior nerf papers accumulate/blend in the sRGB
		# color space. This messes not only with background
		# alpha, but also with DOF effects and the likes.
		# We support this behavior, but we only enable it
		# for the case of synthetic nerf data where we need
		# to compare PSNR numbers to results of prior work.
		testbed.color_space = ngp.ColorSpace.SRGB

		# No exponential cone tracing. Slightly increases
		# quality at the cost of speed. This is done by
		# default on scenes with AABB 1 (like the synthetic
		# ones), but not on larger scenes. So force the
		# setting here.
		testbed.nerf.cone_angle_constant = 0

		# Match nerf paper behaviour and train on a fixed bg.
		testbed.nerf.training.random_bg_color = False

	old_training_step = 0
	n_steps = args.n_steps

	# If we loaded a snapshot, didn't specify a number of steps, _and_ didn't open a GUI,
	# don't train by default and instead assume that the goal is to render screenshots,
	# compute PSNR, or render a video.
	if n_steps < 0 and (not args.load_snapshot or args.gui):
		n_steps = 35000

	tqdm_last_update = 0
	if n_steps > 0:
		with tqdm(desc="Training", total=n_steps, unit="steps") as t:
			while testbed.frame():
				if testbed.want_repl():
					repl(testbed)
				# What will happen when training is done?
				if testbed.training_step >= n_steps:
					if args.gui:
						testbed.shall_train = False
					else:
						break

				# Update progress bar
				if testbed.training_step < old_training_step or old_training_step == 0:
					old_training_step = 0
					t.reset()

				now = time.monotonic()
				if now - tqdm_last_update > 0.1:
					t.update(testbed.training_step - old_training_step)
					t.set_postfix(loss=testbed.loss)
					old_training_step = testbed.training_step
					tqdm_last_update = now

	if args.save_snapshot:
		os.makedirs(os.path.dirname(args.save_snapshot), exist_ok=True)
		testbed.save_snapshot(args.save_snapshot, False)

	if args.test_transforms:
		print("Evaluating test transforms from ", args.test_transforms)
		
		with open(args.test_transforms, 'rb') as f:  # Use 'rb' mode for orjson
			test_transforms = orjson.loads(f.read())

		data_dir=os.path.dirname(args.test_transforms)
		totmse = 0
		totpsnr = 0
		totssim = 0
		totcount = 0
		minpsnr = 1000
		maxpsnr = 0

		# Evaluate metrics on black background
		testbed.background_color = [0.0, 0.0, 0.0, 1.0]

		# Prior nerf papers don't typically do multi-sample anti aliasing.
		# So snap all pixels to the pixel centers.
		testbed.snap_to_pixel_centers = True
		spp = 8

		testbed.nerf.render_min_transmittance = 1e-4

		testbed.shall_train = False
		testbed.fov = np.degrees(test_transforms["camera_angle_x"])
				

		# Move all transforms to GPU at once
		num_frames = len(test_transforms["frames"])
		# res_vals = [512,512]

		# Set the precision for tensors
		precision = torch.float16



		############# METHOD2:  Batch process on GPU ############################
		# Threshold for color conversion
		limit = 0.0031308  

		# Preallocate arrays
		# batch_size = 1024  # Adjust batch size based on GPU memory
		batch_size = 10000
		# image_tensors = torch.zeros((min(batch_size, num_frames), res_vals[0], res_vals[1], 3), dtype=torch.uint8, device="cuda")
		out_vals_arr = torch.zeros((num_frames, 1, 1, 3), dtype=torch.uint8, device="cuda")

		# Function for sRGB to linear conversion (fused ops for efficiency)
		def srgb_to_linear(tensor):
			tensor = torch.where(
				tensor > limit,
				1.055 * torch.pow(tensor, 1.0 / 2.4) - 0.055,
				12.92 * tensor
			)
			return torch.clamp(tensor, 0.0, 1.0) * 255.0 + 0.5

		# Process in batches
		num_batches = (num_frames + batch_size - 1) // batch_size  # Calculate the number of batches

		# for batch_idx in tqdm(range(num_batches)):
		for batch_idx in tqdm(range(num_batches)):      ################ Do something for the last batch
			# Calculate start and end indices for this batch
			start_idx = batch_idx * batch_size
			end_idx = min((batch_idx + 1) * batch_size, num_frames)
			batch_size_actual = end_idx - start_idx

			print(start_idx, end_idx, batch_size_actual)
			# Preallocate batch tensors
			batch_out_vals = torch.zeros((batch_size_actual, 1, 1, 3), device="cuda", dtype=precision)

			cam_matrix_slice = test_transforms["frames"][start_idx:end_idx]
			cam_matrix_list = [np.matrix(matrix["transform_matrix"])[:-1,:] for matrix in cam_matrix_slice]

			# Render the 1x1 output values
			out_vals = torch.tensor(
				testbed.render_parallel(100, 100, spp, True, cam_matrix_list)[:, :, :3],
				device="cuda",
				dtype=precision
			)
			# batch_out_vals = out_vals

			# Apply sRGB to linear conversion for the entire batch
			batch_out_vals = srgb_to_linear(out_vals)
			batch_out_vals = batch_out_vals.reshape(batch_size ,1 ,1 ,3)
			out_vals_arr[start_idx:end_idx] = batch_out_vals[:batch_size_actual].to(dtype=torch.uint8)

		# Save output values
		os.makedirs(os.path.dirname(args.save_dir), exist_ok=True)
		torch.save(out_vals_arr, args.save_dir)

	if args.save_mesh:
		res = args.marching_cubes_res or 256
		thresh = args.marching_cubes_density_thresh or 2.5
		print(f"Generating mesh via marching cubes and saving to {args.save_mesh}. Resolution=[{res},{res},{res}], Density Threshold={thresh}")
		testbed.compute_and_save_marching_cubes_mesh(args.save_mesh, [res, res, res], thresh=thresh)

	if ref_transforms:
		testbed.fov_axis = 0
		testbed.fov = ref_transforms["camera_angle_x"] * 180 / np.pi
		if not args.screenshot_frames:
			args.screenshot_frames = range(len(ref_transforms["frames"]))
		print(args.screenshot_frames)
		for idx in args.screenshot_frames:
			f = ref_transforms["frames"][int(idx)]
			cam_matrix = f.get("transform_matrix", f["transform_matrix_start"])
			testbed.set_nerf_camera_matrix(np.matrix(cam_matrix)[:-1,:])
			outname = os.path.join(args.screenshot_dir, os.path.basename(f["file_path"]))

			# Some NeRF datasets lack the .png suffix in the dataset metadata
			if not os.path.splitext(outname)[1]:
				outname = outname + ".png"

			print(f"rendering {outname}")
			image = testbed.render(args.width or int(ref_transforms["w"]), args.height or int(ref_transforms["h"]), args.screenshot_spp, True)
			os.makedirs(os.path.dirname(outname), exist_ok=True)
			write_image(outname, image)
	elif args.screenshot_dir:
		outname = os.path.join(args.screenshot_dir, args.scene + "_" + network_stem)
		print(f"Rendering {outname}.png")
		image = testbed.render(args.width or 1920, args.height or 1080, args.screenshot_spp, True)
		if os.path.dirname(outname) != "":
			os.makedirs(os.path.dirname(outname), exist_ok=True)
		write_image(outname + ".png", image)

	if args.video_camera_path:
		testbed.load_camera_path(args.video_camera_path)

		resolution = [args.width or 1920, args.height or 1080]
		n_frames = args.video_n_seconds * args.video_fps
		save_frames = "%" in args.video_output
		start_frame, end_frame = args.video_render_range

		if "tmp" in os.listdir():
			shutil.rmtree("tmp")
		os.makedirs("tmp")

		for i in tqdm(list(range(min(n_frames, n_frames+1))), unit="frames", desc=f"Rendering video"):
			testbed.camera_smoothing = args.video_camera_smoothing

			if start_frame >= 0 and i < start_frame:
				# For camera smoothing and motion blur to work, we cannot just start rendering
				# from middle of the sequence. Instead we render a very small image and discard it
				# for these initial frames.
				# TODO Replace this with a no-op render method once it's available
				frame = testbed.render(32, 32, 1, True, float(i)/n_frames, float(i + 1)/n_frames, args.video_fps, shutter_fraction=0.5)
				continue
			elif end_frame >= 0 and i > end_frame:
				continue

			frame = testbed.render(resolution[0], resolution[1], args.video_spp, True, float(i)/n_frames, float(i + 1)/n_frames, args.video_fps, shutter_fraction=0.5)
			if save_frames:
				write_image(args.video_output % i, np.clip(frame * 2**args.exposure, 0.0, 1.0), quality=100)
			else:
				write_image(f"tmp/{i:04d}.jpg", np.clip(frame * 2**args.exposure, 0.0, 1.0), quality=100)

		if not save_frames:
			os.system(f"ffmpeg -y -framerate {args.video_fps} -i tmp/%04d.jpg -c:v libx264 -pix_fmt yuv420p {args.video_output}")

		shutil.rmtree("tmp")
