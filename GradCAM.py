import cv2
import os
import numpy as np
from PIL import Image
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as transforms


class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.conv1 = nn.Conv2d(3, 6, 5)
        self.pool1 = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(6, 16, 5)
        self.pool2 = nn.MaxPool2d(2, 2)
        self.fc1 = nn.Linear(16 * 5 * 5, 120)
        self.fc2 = nn.Linear(120, 84)
        self.fc3 = nn.Linear(84, 10)

    def forward(self, x):
        x = self.pool1(F.relu(self.conv1(x)))
        x = self.pool1(F.relu(self.conv2(x)))
        x = x.view(-1, 16 * 5 * 5)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x


class Model_w_GradCAM():
    def __init__(self, model: torch.nn.Module, category_index: int = None, aimed_module: str = None):
        # 给了model，就知道了默认要取的layer，输出类别数。
        self.model = model
        self.model_items = list(self.model._modules.items())
        self.model_items.reverse()
        self.get_classes()
        self.chose_module(aimed_module)
        self.set_class_index(category_index)
        self.set_hook()
        pass

    def set_hook(self):
        def forward_hook(module, input, output):
            self.feature_map = output.detach().cpu()  # bs,channels,size,size

        def backward_hook(module, grad_in, grad_out):
            self.grad_map = grad_out[0].detach().cpu()

        self.aimed_module.register_forward_hook(forward_hook)
        self.aimed_module.register_backward_hook(backward_hook)

    def get_classes(self):
        # 数有多少类
        last_layer = self.model_items[0][1]
        self.num_classes = last_layer.out_features

    def chose_module(self, aimed_module):
        # 选择要可视化的最后一个卷积层，有值就按名字选
        module = None
        for name, module in self.model_items:
            if not aimed_module:
                if isinstance(module, (torch.nn.modules.conv._ConvNd,)):
                    break
            else:
                if name == aimed_module:
                    break
        assert module != None
        self.aimed_module = module

    def set_class_index(self, category_index):
        # 设置固定类别
        if not category_index:

            self.category_index = None
        else:
            assert isinstance(category_index, int)
            assert category_index < self.num_classes
            self.category_index = category_index
            one_hot = np.zeros((1, self.num_classes), np.float32)
            one_hot[0, category_index] = 1
            self.one_hot = torch.from_numpy(one_hot)
            self.one_hot.requires_grad_(True)

    def draw_cam(self, img, pred):
        # img: RGB
        # pred: shape=1,c
        # 求梯度
        self.model.zero_grad()
        if not self.category_index:
            # 没有类别，就按最大值来
            pred_max = torch.max(pred, 1)
            pred = torch.zeros_like(pred)
            pred[torch.arange(pred.shape[0]), pred_max.indices] = pred_max.values
        else:
            pred = pred * self.one_hot
        # 必须独立求梯度,只能传一张
        class_loss = torch.sum(pred)
        class_loss.backward(retain_graph=True)
        # 可视化图
        self.grad_map = torch.mean(self.grad_map, [2, 3], keepdim=True)  # 1,c,1,1
        cam = self.grad_map[0] * self.feature_map[0]  # c,mH,mW
        cam = torch.sum(cam, 0).numpy()  # mH,mW
        heatmap = self.heatmap(img, cam)
        return heatmap

    def heatmap(self, img, cam):
        img = np.float32(img) / 255
        cam = cv2.resize(cam, (img.shape[1], img.shape[0]))
        cam = np.maximum(cam, 0)
        cam = (cam - cam.min()) / (cam.max() - cam.min())
        heatmap = cv2.applyColorMap(np.uint8(255 * cam), cv2.COLORMAP_JET)
        heatmap = np.float32(heatmap) / 255
        # 附着
        heatmap = heatmap[..., ::-1] * 0.4 + np.float32(img)
        heatmap = heatmap / np.max(heatmap)
        heatmap = np.uint8(heatmap * 255)
        return heatmap

    def __call__(self, *args, **kwargs):
        assert args[0].shape[0] == 1
        pred = self.model(*args, **kwargs)  # softmax之前
        return pred
        pass


def img_preprocess(img_in):
    """
    读取图片，转为模型输入
    :param img_in: ndarray, [1,H, W, C]
    :return: PIL.image
    """
    img = img_in.copy()
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize([0.4948052, 0.48568845, 0.44682974], [0.24580306, 0.24236229, 0.2603115])
    ])
    img = Image.fromarray(np.uint8(img))
    img = transform(img)
    img = img.unsqueeze(0)
    return img


def save_img_cam(img, cam, out_dir):
    path_cam_img = os.path.join(out_dir, "cam.jpg")
    path_raw_img = os.path.join(out_dir, "raw.jpg")
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
    cv2.imwrite(path_cam_img, np.uint8(255 * cam))
    cv2.imwrite(path_raw_img, np.uint8(255 * img))


if __name__ == '__main__':
    print('for example!')

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    path_img = os.path.join(BASE_DIR, "cam_img", "test_img_8.png")
    path_net = os.path.join(BASE_DIR,  "paras.pkl")
    output_dir = os.path.join(BASE_DIR, "result")

    classes = ('plane', 'car', 'bird', 'cat', 'deer', 'dog', 'frog', 'horse', 'ship', 'truck')

    img = cv2.imread(path_img, 1)  # H*W*C
    img = cv2.resize(img, (32, 32))
    img = img[:, :, ::-1]  # BGR --> RGB
    img_input = img_preprocess(img)
    net = Net()
    net.load_state_dict(torch.load(path_net))
    net = Model_w_GradCAM(net)
    output = net(img_input)
    print(classes[torch.argmax(output.cpu(),1)])
    cam = net.draw_cam(img, output)
    from matplotlib import pyplot as plt
    plt.imshow(cam);plt.show();plt.imsave(os.path.join(output_dir,'cam.png'),cam)
    plt.imshow(img);plt.show();plt.imsave(os.path.join(output_dir,'img.png'),img)